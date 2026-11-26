"""
Algorithm 1: Historical Artifact Discovery and Collection.

This module discovers archived captures, retrieves their content, computes
SHA-256 hashes, and stores content plus provenance metadata. It deliberately
does not perform entity resolution or timeline reconstruction.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urldefrag, urljoin, urlparse, urlunparse

import requests

from src.hdfr.storage import ArtifactStorage

try:
    from warcio.archiveiterator import ArchiveIterator
except ImportError:  # A clear error is raised only if Common Crawl retrieval is used.
    ArchiveIterator = None


WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
COMMON_CRAWL_INDEX_URL = "https://index.commoncrawl.org"
COMMON_CRAWL_INFO_URL = f"{COMMON_CRAWL_INDEX_URL}/collinfo.json"
COMMON_CRAWL_DATA_URL = "https://data.commoncrawl.org"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
MAX_WARC_RECORD_BYTES = 100_000_000
USER_AGENT = "PAHDFR-Historical-Artifact-Collector/1.1"

logger = logging.getLogger(__name__)


class _LinkExtractor(HTMLParser):
    """Collect anchor destinations from an HTML page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


def get_current_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def calculate_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def create_artifact_id(source_uri: str, content_hash: str) -> str:
    raw_value = f"{source_uri}:{content_hash}"
    return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def ensure_directory(directory: str | Path) -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_url(value: str) -> str:
    """Validate and normalize an HTTP(S) URL."""
    if not isinstance(value, str):
        raise ValueError("target_url must be a string")
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("target_url must be a valid HTTP or HTTPS URL")
    return value


def validate_date(value: str | None, field_name: str) -> None:
    if value is None:
        return
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError(f"{field_name} must use YYYYMMDD format")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{field_name} is not a valid calendar date") from exc


class HistoricalArtifactCollector:
    """Discover and collect archived artifacts from Wayback and Common Crawl."""

    def __init__(
        self,
        storage_dir: str = "data/artifacts",
        timeout: int = REQUEST_TIMEOUT,
        database_path: str = "data/hdfr.db",
        retry_wait: float = 2.0,
        max_retries: int = MAX_RETRIES,
    ):
        self.storage_dir = ensure_directory(storage_dir)
        self.timeout = timeout
        self.retry_wait = max(0.0, retry_wait)
        self.max_retries = max(1, max_retries)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.errors: list[dict[str, Any]] = []
        self.storage = ArtifactStorage(database_path)
        self._common_crawl_indexes_cache: dict[str, list[str]] = {}

    def record_error(
        self,
        source: str,
        stage: str,
        message: str,
        url: str | None = None,
    ) -> None:
        error = {
            "source": source,
            "stage": stage,
            "url": url,
            "message": message,
            "timestamp": get_current_time(),
        }
        self.errors.append(error)
        logger.error(
            "Source: %s | Stage: %s | URL: %s | Error: %s",
            source, stage, url, message,
        )
        try:
            self.storage.save_error(error)
        except Exception:
            # A database error must not hide the original collection error.
            logger.exception("Could not persist collection error to SQLite")

    def _get_with_retry(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        source: str,
    ) -> requests.Response:
        """GET with bounded retries for rate limits and temporary server errors."""
        retryable = {429, 500, 502, 503, 504}
        last_response = None

        for attempt in range(self.max_retries):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt == self.max_retries - 1:
                    raise
                wait_time = min(max(self.retry_wait * (2 ** attempt), 0.5), 30.0)
                logger.warning(
                    "%s request failed (%s). Retrying in %.1f seconds (%d/%d).",
                    source, exc, wait_time, attempt + 1, self.max_retries,
                )
                time.sleep(wait_time)
                continue

            last_response = response
            if response.status_code not in retryable:
                response.raise_for_status()
                return response

            if attempt == self.max_retries - 1:
                response.raise_for_status()

            retry_after = response.headers.get("Retry-After")
            try:
                wait_time = float(retry_after)
            except (TypeError, ValueError):
                wait_time = self.retry_wait * (2 ** attempt)

            wait_time = min(max(wait_time, 0.5), 30.0)
            logger.warning(
                "%s returned HTTP %s. Retrying in %.1f seconds (%d/%d).",
                source, response.status_code, wait_time,
                attempt + 1, self.max_retries,
            )
            time.sleep(wait_time)

        # Defensive: the loop either returns or raises.
        if last_response is not None:
            last_response.raise_for_status()
        raise requests.RequestException(f"{source} request failed")

    # ----------------------- Wayback discovery -----------------------

    def discover_internal_links(
        self,
        start_url: str,
        *,
        max_pages: int = 25,
        max_depth: int = 2,
        delay_seconds: float = 0.5,
    ) -> list[str]:
        """Crawl a bounded set of same-host HTML pages to discover URLs.

        The live pages are used only to discover links. Their contents are not
        stored as artifacts; each returned URL is searched in the selected web
        archives by the caller.
        """
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if max_depth < 0:
            raise ValueError("max_depth cannot be negative")

        start_url = validate_url(start_url)
        start_parts = urlparse(start_url)
        root_host = (start_parts.hostname or "").casefold()
        root_port = start_parts.port

        def canonicalize(candidate: str) -> str | None:
            candidate, _fragment = urldefrag(candidate.strip())
            parsed = urlparse(candidate)
            if parsed.scheme.casefold() not in {"http", "https"}:
                return None
            if (parsed.hostname or "").casefold() != root_host or parsed.port != root_port:
                return None
            # Discard credentials and normalize the host while keeping paths
            # and query strings, which can identify different public pages.
            host = root_host
            if root_port is not None:
                host = f"{host}:{root_port}"
            return urlunparse((parsed.scheme.casefold(), host, parsed.path or "/", "", parsed.query, ""))

        start_url = canonicalize(start_url) or start_url
        queue = deque([(start_url, 0)])
        scheduled = {start_url}
        discovered: list[str] = []

        robots_url = f"{start_parts.scheme}://{start_parts.netloc}/robots.txt"
        robots_lines: list[str] = []
        crawl_timeout = min(self.timeout, 10)
        try:
            robots_response = self.session.get(robots_url, timeout=crawl_timeout)
            if robots_response.status_code == 404:
                robots_lines = []
            elif robots_response.ok:
                robots_lines = robots_response.text.splitlines()
            else:
                self.record_error(
                    "crawler", "robots", f"robots.txt returned HTTP {robots_response.status_code}; "
                    "link crawl skipped", robots_url,
                )
                return [start_url]
        except requests.RequestException as exc:
            self.record_error("crawler", "robots", f"Could not read robots.txt: {exc}", robots_url)
            return [start_url]

        from urllib.robotparser import RobotFileParser

        robots = RobotFileParser()
        robots.set_url(robots_url)
        robots.parse(robots_lines)
        crawl_delay = robots.crawl_delay("*") or delay_seconds
        crawl_delay = min(max(float(crawl_delay), 0.1), 10.0)

        while queue and len(discovered) < max_pages:
            current_url, depth = queue.popleft()
            discovered.append(current_url)
            if depth >= max_depth:
                continue
            if not robots.can_fetch(USER_AGENT, current_url):
                self.record_error(
                    "crawler", "robots", "robots.txt disallows crawling this page", current_url,
                )
                continue

            try:
                response = self.session.get(current_url, timeout=crawl_timeout)
                response.raise_for_status()
                final = urlparse(response.url)
                if (final.hostname or "").casefold() != root_host:
                    self.record_error(
                        "crawler", "redirect", "Page redirected outside the starting host; skipped",
                        current_url,
                    )
                    continue
                if "html" not in response.headers.get("Content-Type", "").casefold():
                    continue

                parser = _LinkExtractor()
                parser.feed(response.text)
                for href in parser.links:
                    candidate = canonicalize(urljoin(response.url, href))
                    if candidate is None or candidate in scheduled:
                        continue
                    if len(scheduled) >= max_pages:
                        break
                    scheduled.add(candidate)
                    queue.append((candidate, depth + 1))
            except (requests.RequestException, ValueError) as exc:
                self.record_error("crawler", "link_discovery", str(exc), current_url)

            if queue:
                time.sleep(crawl_delay)

        return discovered

    def discover_wayback(
        self,
        target_url: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "url": target_url,
            "output": "json",
            "fl": "timestamp,original,statuscode,mimetype,digest",
            "filter": "statuscode:200",
            "collapse": "digest",
        }
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        try:
            response = self._get_with_retry(
                WAYBACK_CDX_URL, params=params, source="Wayback CDX",
            )
            rows = response.json()
            if not isinstance(rows, list) or not rows:
                return []

            headers = rows[0]
            if not isinstance(headers, list):
                raise ValueError("Unexpected Wayback CDX response header")

            artifacts = []
            for row in rows[1:]:
                if not isinstance(row, list):
                    continue
                record = dict(zip(headers, row))
                timestamp = record.get("timestamp")
                original_url = record.get("original")
                if not timestamp or not original_url:
                    continue

                artifacts.append({
                    "source": "wayback",
                    "source_uri": original_url,
                    "archive_uri": (
                        f"https://web.archive.org/web/{timestamp}id_/{original_url}"
                    ),
                    "capture_time": timestamp,
                    "publication_time": None,
                    "mime_type": record.get("mimetype"),
                    "archive_digest": record.get("digest"),
                })
            return artifacts

        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            self.record_error("wayback", "discovery", str(exc), target_url)
            return []

    # --------------------- Common Crawl discovery --------------------

    def _get_common_crawl_indexes(self, preferred_index: str) -> list[str]:
        """Return preferred index followed by recent indexes from collinfo.json."""
        if preferred_index in self._common_crawl_indexes_cache:
            return list(self._common_crawl_indexes_cache[preferred_index])
        indexes = [preferred_index] if preferred_index else []
        try:
            response = self._get_with_retry(
                COMMON_CRAWL_INFO_URL, source="Common Crawl index catalog",
            )
            catalog = response.json()
            if isinstance(catalog, list):
                for item in catalog:
                    index_id = item.get("id") if isinstance(item, dict) else None
                    if index_id and index_id not in indexes:
                        indexes.append(index_id)
        except (requests.RequestException, ValueError, TypeError) as exc:
            logger.warning("Could not retrieve Common Crawl index catalog: %s", exc)
            # Known historical indexes are a last-resort fallback when the
            # catalog endpoint itself is unavailable.
            for fallback in (
                "CC-MAIN-2025-30",
                "CC-MAIN-2025-26",
                "CC-MAIN-2025-21",
                "CC-MAIN-2025-18",
            ):
                if fallback not in indexes:
                    indexes.append(fallback)

        # Limit fallback work so an unavailable service does not stall the run.
        indexes = indexes[:5]
        self._common_crawl_indexes_cache[preferred_index] = indexes
        return list(indexes)

    def discover_common_crawl(
        self,
        target_url: str,
        index_name: str = "CC-MAIN-2025-30",
    ) -> list[dict[str, Any]]:
        indexes = self._get_common_crawl_indexes(index_name)
        all_artifacts: list[dict[str, Any]] = []
        seen = set()
        last_error = None
        successful_index_queries = 0

        for current_index in indexes:
            index_url = (
                f"{COMMON_CRAWL_INDEX_URL}/"
                f"{quote(current_index, safe='')}-index"
            )
            params = {
                "url": target_url,
                "output": "json",
                "filter": "status:200",
            }

            try:
                response = self._get_with_retry(
                    index_url, params=params, source=f"Common Crawl {current_index}",
                )
                successful_index_queries += 1
                for line in response.text.splitlines():
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    timestamp = record.get("timestamp")
                    original_url = record.get("url")
                    filename = record.get("filename")
                    offset = record.get("offset")
                    length = record.get("length")

                    if not all((timestamp, original_url, filename)) or offset is None or length is None:
                        continue

                    offset = int(offset)
                    length = int(length)
                    if offset < 0 or length <= 0 or length > MAX_WARC_RECORD_BYTES:
                        continue

                    key = (timestamp, original_url, filename, offset)
                    if key in seen:
                        continue
                    seen.add(key)

                    all_artifacts.append({
                        "source": "common_crawl",
                        "source_uri": original_url,
                        "archive_uri": (
                            f"{COMMON_CRAWL_DATA_URL}/{filename.lstrip('/')}"
                            f"#offset={offset}&length={length}"
                        ),
                        "capture_time": timestamp,
                        "publication_time": None,
                        "filename": filename,
                        "offset": offset,
                        "length": length,
                        "index_name": current_index,
                        "mime_type": record.get("mime"),
                        "warc_record_id": record.get("warc-record-id"),
                    })

                # One usable index is enough for this run. Try another index
                # only when this index returned no matching captures.
                if all_artifacts:
                    break

            except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
                last_error = exc
                logger.warning(
                    "Common Crawl index %s could not be queried: %s",
                    current_index, exc,
                )
                continue

        if not all_artifacts and last_error is not None:
            if successful_index_queries:
                message = (
                    "No captures were returned by the queried indexes; "
                    f"at least one index request also failed. Last error: {last_error}"
                )
            else:
                message = f"All attempted index requests failed. Last error: {last_error}"
            self.record_error("common_crawl", "discovery", message, target_url)
        return all_artifacts

    # -------------------------- Retrieval ----------------------------

    def retrieve_wayback(self, artifact: dict[str, Any]) -> bytes | None:
        try:
            response = self._get_with_retry(
                artifact["archive_uri"], source="Wayback snapshot",
            )
            if not response.content:
                raise ValueError("Wayback returned an empty response body")
            return response.content
        except (requests.RequestException, KeyError, ValueError) as exc:
            self.record_error(
                "wayback", "retrieval", str(exc), artifact.get("archive_uri"),
            )
            return None

    def retrieve_common_crawl(self, artifact: dict[str, Any]) -> bytes | None:
        if ArchiveIterator is None:
            self.record_error(
                "common_crawl", "retrieval",
                "warcio is not installed. Install it with: py -m pip install warcio",
                artifact.get("archive_uri"),
            )
            return None

        try:
            filename = str(artifact["filename"])
            offset = int(artifact["offset"])
            length = int(artifact["length"])
            if offset < 0 or length <= 0 or length > MAX_WARC_RECORD_BYTES:
                raise ValueError("Invalid or oversized WARC byte range")

            warc_url = f"{COMMON_CRAWL_DATA_URL}/{filename.lstrip('/')}"
            headers = {"Range": f"bytes={offset}-{offset + length - 1}"}
            response = self._get_with_retry(
                warc_url, headers=headers, source="Common Crawl WARC retrieval",
            )
            if response.status_code != 206:
                raise requests.RequestException(
                    f"Expected HTTP 206 Partial Content, got {response.status_code}"
                )

            # A WARC response is a container. Extract the HTTP payload, not the
            # compressed/container bytes, before hashing and storing the artifact.
            for record in ArchiveIterator(io.BytesIO(response.content)):
                if record.rec_type == "response":
                    payload = record.content_stream().read()
                    if payload:
                        return payload
            raise ValueError("No HTTP response payload found in WARC record")

        except (requests.RequestException, KeyError, ValueError, TypeError, OSError) as exc:
            self.record_error(
                "common_crawl", "retrieval", str(exc), artifact.get("archive_uri"),
            )
            return None

    # ---------------------------- Storage ---------------------------

    def store_artifact(
        self,
        artifact: dict[str, Any],
        content: bytes,
    ) -> dict[str, Any] | None:
        try:
            if not content:
                raise ValueError("Artifact content is empty")

            content_hash = calculate_sha256(content)
            source_uri = artifact["source_uri"]
            artifact_id = create_artifact_id(source_uri, content_hash)
            artifact_dir = self.storage_dir / artifact_id
            artifact_dir.mkdir(parents=True, exist_ok=True)

            content_path = artifact_dir / "content.bin"
            metadata_path = artifact_dir / "metadata.json"
            content_path.write_bytes(content)

            metadata = {
                "artifact_id": artifact_id,
                "source": artifact.get("source"),
                "source_uri": source_uri,
                "archive_uri": artifact.get("archive_uri"),
                "capture_time": artifact.get("capture_time"),
                "collection_time": get_current_time(),
                "publication_time": artifact.get("publication_time"),
                "event_time": None,
                "content_hash": content_hash,
                "hash_algorithm": "SHA-256",
                "storage_path": str(content_path.resolve()),
                "mime_type": artifact.get("mime_type"),
                "entity_id": None,
                "evidence_confidence": None,
                "supporting_artifacts": [],
                "collection_status": "success",
                "archive_digest": artifact.get("archive_digest"),
                "index_name": artifact.get("index_name"),
                "warc_filename": artifact.get("filename"),
                "warc_offset": artifact.get("offset"),
                "warc_length": artifact.get("length"),
                "warc_record_id": artifact.get("warc_record_id"),
            }

            metadata_path.write_text(
                json.dumps(metadata, indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
            self.storage.save_artifact(metadata)
            return metadata

        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.record_error(
                artifact.get("source", "unknown"), "storage",
                str(exc), artifact.get("source_uri"),
            )
            return None

    # -------------------------- Orchestration -----------------------

    def run(
        self,
        target_url: str,
        sources: list[str] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        common_crawl_index: str = "CC-MAIN-2025-30",
        crawl_site: bool = False,
        max_crawl_pages: int = 25,
        crawl_depth: int = 2,
    ) -> dict[str, Any]:
        target_url = validate_url(target_url)
        validate_date(from_date, "from_date")
        validate_date(to_date, "to_date")
        if from_date and to_date and from_date > to_date:
            raise ValueError("from_date cannot be later than to_date")

        if sources is None:
            sources = ["wayback", "common_crawl"]
        if not sources:
            raise ValueError("At least one archival source must be selected")
        invalid_sources = set(sources) - {"wayback", "common_crawl"}
        if invalid_sources:
            raise ValueError(f"Unsupported sources: {sorted(invalid_sources)}")

        self.errors = []
        started_at = get_current_time()
        discovered: list[dict[str, Any]] = []
        collected: list[dict[str, Any]] = []

        target_urls = [target_url]
        if crawl_site:
            target_urls = self.discover_internal_links(
                target_url,
                max_pages=max_crawl_pages,
                max_depth=crawl_depth,
            )

        for page_url in target_urls:
            if "wayback" in sources:
                discovered.extend(self.discover_wayback(page_url, from_date, to_date))
            if "common_crawl" in sources:
                discovered.extend(self.discover_common_crawl(page_url, common_crawl_index))

        # Remove duplicate source/capture/location records before downloading.
        unique_discovered = []
        seen = set()
        for item in discovered:
            key = (
                item.get("source"), item.get("source_uri"),
                item.get("capture_time"), item.get("archive_uri"),
                item.get("filename"), item.get("offset"),
            )
            if key not in seen:
                seen.add(key)
                unique_discovered.append(item)

        for artifact in unique_discovered:
            if artifact["source"] == "wayback":
                content = self.retrieve_wayback(artifact)
            else:
                content = self.retrieve_common_crawl(artifact)
            if content is None:
                continue
            metadata = self.store_artifact(artifact, content)
            if metadata is not None:
                collected.append(metadata)
            time.sleep(0.1)

        finished_at = get_current_time()
        result = {
            "algorithm": "Algorithm 1: Historical Artifact Discovery and Collection",
            "target_url": target_url,
            "crawl_enabled": crawl_site,
            "crawl_page_count": len(target_urls),
            "crawl_depth": crawl_depth if crawl_site else 0,
            "started_at": started_at,
            "finished_at": finished_at,
            "discovered_count": len(unique_discovered),
            "collected_count": len(collected),
            "failed_count": len(self.errors),
            "discovery_error_count": sum(e["stage"] == "discovery" for e in self.errors),
            "retrieval_error_count": sum(e["stage"] == "retrieval" for e in self.errors),
            "storage_error_count": sum(e["stage"] == "storage" for e in self.errors),
            "artifacts": collected,
            "errors": self.errors,
        }
        self.storage.save_run(result)
        return result
