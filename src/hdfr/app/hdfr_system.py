"""Provider-based HDFR collection and evidence storage, separate from Algorithm 2.

Built-in adapters support Wayback CDX, Common Crawl CDXJ, and Arquivo.pt.
Additional providers can be registered through `register_archive_provider`.
The implementation uses only the Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import gzip
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

WAYBACK_CDX_DEFAULT = "https://web.archive.org/cdx/search/cdx"
WAYBACK_ARCHIVE_SERVICE = "Internet Archive Wayback Machine"
USER_AGENT = "PAHDFR-research-prototype/0.1 (historical evidence collection)"


class ArchiveAPIError(RuntimeError):
    """Raised when an archive index or capture cannot be accessed or parsed."""


@dataclass(frozen=True)
class ArchiveConfig:
    provider: str = "wayback"
    # Blank means use the provider's default API endpoint.
    api_base_url: str = ""
    # Blank by default. Wayback CDX does not require a key for normal public queries.
    api_key: str = ""
    commoncrawl_index: str = ""
    capture_url_template: str = ""
    timeout_seconds: int = 30
    user_agent: str = USER_AGENT
    request_delay_seconds: float = 1.0

    @classmethod
    def from_environment(cls) -> "ArchiveConfig":
        """Read optional API settings; blank values preserve the defaults."""
        file_values: dict[str, str] = {}
        env_file = Path(__file__).resolve().parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    file_values[key.strip()] = value.strip().strip("\"'")
        return cls(
            provider=os.getenv(
                "HDFR_ARCHIVE_PROVIDER",
                file_values.get("HDFR_ARCHIVE_PROVIDER", "wayback"),
            ).strip().casefold(),
            api_base_url=os.getenv(
                "HDFR_ARCHIVE_API_BASE_URL",
                file_values.get("HDFR_ARCHIVE_API_BASE_URL", ""),
            ).strip(),
            api_key=os.getenv(
                "HDFR_ARCHIVE_API_KEY", file_values.get("HDFR_ARCHIVE_API_KEY", "")
            ).strip(),
            commoncrawl_index=os.getenv(
                "HDFR_COMMONCRAWL_INDEX", file_values.get("HDFR_COMMONCRAWL_INDEX", "")
            ).strip(),
            capture_url_template=os.getenv(
                "HDFR_CAPTURE_URL_TEMPLATE",
                file_values.get("HDFR_CAPTURE_URL_TEMPLATE", ""),
            ).strip(),
        )


@dataclass(frozen=True)
class Capture:
    timestamp: str
    original_url: str
    mimetype: str
    status_code: str
    archive_digest: str
    length: str
    capture_uri: str
    archive_service: str = WAYBACK_ARCHIVE_SERVICE
    provider_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class CollectionReport:
    discovered: int = 0
    saved: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


class WaybackCDXClient:
    """Search Wayback's CDX index and fetch selected archived captures."""

    def __init__(self, config: ArchiveConfig | None = None) -> None:
        self.config = config or ArchiveConfig.from_environment()
        self.endpoint = self.config.api_base_url or WAYBACK_CDX_DEFAULT
        self._last_request_at = 0.0

    def _get(self, url: str, extra_headers: dict[str, str] | None = None) -> bytes:
        wait = self.config.request_delay_seconds - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        headers = {"User-Agent": self.config.user_agent}
        headers.update(extra_headers or {})
        if self.config.api_key:
            headers["Cookie"] = f"cdx-auth-token={self.config.api_key}"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ArchiveAPIError(f"HTTP request failed: {exc}") from exc
        finally:
            self._last_request_at = time.monotonic()
        return data

    def search_captures(self, url: str, *, limit: int = 25,
                        from_year: int | None = None,
                        to_year: int | None = None) -> list[Capture]:
        """Return indexed HTML captures for a URL, oldest to newest."""
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if from_year is not None and to_year is not None and from_year > to_year:
            raise ValueError("from_year cannot be later than to_year")

        params: list[tuple[str, str]] = [
            ("url", url),
            ("output", "json"),
            ("fl", "timestamp,original,mimetype,statuscode,digest,length"),
            ("filter", "statuscode:200"),
            ("filter", "mimetype:text/html"),
            ("gzip", "false"),
            ("limit", str(limit)),
        ]
        if from_year is not None:
            params.append(("from", str(from_year)))
        if to_year is not None:
            params.append(("to", str(to_year)))
        endpoint = self.endpoint + ("&" if "?" in self.endpoint else "?") + urlencode(params)
        try:
            rows = json.loads(self._get(endpoint).decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveAPIError("CDX response was not valid JSON") from exc
        if not isinstance(rows, list) or not rows:
            return []
        header = rows[0]
        if not isinstance(header, list):
            raise ArchiveAPIError("CDX response did not contain a field header")
        index = {name: position for position, name in enumerate(header)}
        needed = ("timestamp", "original", "mimetype", "statuscode", "digest", "length")
        if any(name not in index for name in needed):
            raise ArchiveAPIError("CDX response is missing required fields")

        captures: list[Capture] = []
        for row in rows[1:]:
            if not isinstance(row, list) or len(row) < len(header):
                continue
            timestamp = row[index["timestamp"]]
            original = row[index["original"]]
            captures.append(Capture(
                timestamp=timestamp,
                original_url=original,
                mimetype=row[index["mimetype"]],
                status_code=row[index["statuscode"]],
                archive_digest=row[index["digest"]],
                length=row[index["length"]],
                capture_uri=self._capture_uri(timestamp, original),
                archive_service=(
                    "Internet Archive Wayback Machine"
                    if self.config.provider == "wayback" else "Custom CDX-compatible archive"
                ),
            ))
        return captures

    def _capture_uri(self, timestamp: str, original_url: str) -> str:
        if self.config.provider == "wayback":
            return f"https://web.archive.org/web/{timestamp}id_/{original_url}"
        if not self.config.capture_url_template:
            raise ArchiveAPIError(
                "Custom CDX provider requires HDFR_CAPTURE_URL_TEMPLATE with "
                "{timestamp} and {original_url} placeholders"
            )
        return self.config.capture_url_template.format(
            timestamp=timestamp,
            original_url=quote(original_url, safe=":/?=&%#"),
        )

    def fetch_capture(self, capture: Capture) -> bytes:
        """Download the archived response body for a CDX capture."""
        return self._get(capture.capture_uri)


class CommonCrawlClient(WaybackCDXClient):
    """Search a selected Common Crawl index and fetch its WARC byte ranges."""

    SERVICE = "Common Crawl"
    INDEX_LIST_URL = "https://index.commoncrawl.org/collinfo.json"

    def _index_endpoint(self) -> str:
        if self.config.api_base_url:
            return self.config.api_base_url
        if self.config.commoncrawl_index:
            index = self.config.commoncrawl_index
            if index.startswith("http://") or index.startswith("https://"):
                return index
            return f"https://index.commoncrawl.org/{index.removesuffix('-index')}-index"
        try:
            indexes = json.loads(self._get(self.INDEX_LIST_URL).decode("utf-8"))
            if not isinstance(indexes, list) or not indexes:
                raise ArchiveAPIError("Common Crawl returned no available indexes")
            endpoint = indexes[0].get("cdx-api")
            if not endpoint:
                raise ArchiveAPIError("Latest Common Crawl index has no CDX API URL")
            return endpoint
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveAPIError("Could not parse the Common Crawl index list") from exc

    def search_captures(self, url: str, *, limit: int = 25,
                        from_year: int | None = None,
                        to_year: int | None = None) -> list[Capture]:
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if from_year is not None and to_year is not None and from_year > to_year:
            raise ValueError("from_year cannot be later than to_year")
        params: list[tuple[str, str]] = [
            ("url", url), ("output", "json"), ("limit", str(limit)),
            ("filter", "status:200"), ("filter", "mime:text/html"),
        ]
        if from_year is not None:
            params.append(("from", str(from_year)))
        if to_year is not None:
            params.append(("to", str(to_year)))
        index_endpoint = self._index_endpoint()
        endpoint = index_endpoint + "?" + urlencode(params)
        try:
            lines = self._get(endpoint).decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as exc:
            raise ArchiveAPIError("Common Crawl returned non-text index data") from exc
        captures: list[Capture] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                filename = item["filename"]
                offset = int(item["offset"])
                length = int(item["length"])
                timestamp = item["timestamp"]
                original = item.get("url", url)
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ArchiveAPIError("Malformed Common Crawl CDXJ result") from exc
            if from_year is not None and int(timestamp[:4]) < from_year:
                continue
            if to_year is not None and int(timestamp[:4]) > to_year:
                continue
            data_url = "https://data.commoncrawl.org/" + filename.lstrip("/")
            captures.append(Capture(
                timestamp=timestamp,
                original_url=original,
                mimetype=item.get("mime-detected", item.get("mime", "text/html")),
                status_code=str(item.get("status", "200")),
                archive_digest=item.get("digest", ""),
                length=str(length),
                capture_uri=f"{data_url}#offset={offset}&length={length}",
                archive_service=self.SERVICE,
                provider_data={
                    "format": "commoncrawl-warc-gzip",
                    "data_url": data_url,
                    "offset": offset,
                    "length": length,
                    "crawl_index": index_endpoint,
                    "filename": filename,
                },
            ))
        return captures

    def fetch_capture(self, capture: Capture) -> bytes:
        details = capture.provider_data
        start = int(details["offset"])
        length = int(details["length"])
        return self._get(
            details["data_url"],
            {"Range": f"bytes={start}-{start + length - 1}"},
        )


class ArquivoPTClient(WaybackCDXClient):
    """Use Arquivo.pt's CDX-compatible URL history API and replay URLs."""

    SERVICE = "Arquivo.pt"
    DEFAULT_ENDPOINT = "https://arquivo.pt/wayback/cdx"

    def __init__(self, config: ArchiveConfig | None = None) -> None:
        super().__init__(config)
        self.endpoint = self.config.api_base_url or self.DEFAULT_ENDPOINT

    def search_captures(self, url: str, *, limit: int = 25,
                        from_year: int | None = None,
                        to_year: int | None = None) -> list[Capture]:
        if not url.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        params: list[tuple[str, str]] = [("url", url), ("output", "json"), ("limit", str(limit))]
        if from_year is not None:
            params.append(("from", str(from_year)))
        if to_year is not None:
            params.append(("to", str(to_year)))
        try:
            response_text = self._get(
                self.endpoint + "?" + urlencode(params)
            ).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ArchiveAPIError("Arquivo.pt returned non-text CDX data") from exc
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            try:
                payload = [json.loads(line) for line in response_text.splitlines() if line.strip()]
            except json.JSONDecodeError as exc:
                raise ArchiveAPIError("Arquivo.pt returned an unreadable CDX response") from exc

        records: list[dict[str, Any]] = []
        if isinstance(payload, list) and payload and isinstance(payload[0], list):
            header = payload[0]
            records = [dict(zip(header, row)) for row in payload[1:] if isinstance(row, list)]
        elif isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
            records = payload
        elif isinstance(payload, dict):
            maybe_records = payload.get("captures", payload.get("results", []))
            if isinstance(maybe_records, list):
                records = [item for item in maybe_records if isinstance(item, dict)]

        captures: list[Capture] = []
        for item in records:
            timestamp = str(item.get("timestamp", ""))
            original = item.get("original") or item.get("url")
            if not timestamp or not original:
                continue
            mimetype = str(item.get("mimetype", item.get("mime", "text/html")))
            status = str(item.get("statuscode", item.get("status", "200")))
            if status != "200" or "html" not in mimetype.casefold():
                continue
            if from_year is not None and int(timestamp[:4]) < from_year:
                continue
            if to_year is not None and int(timestamp[:4]) > to_year:
                continue
            replay_uri = item.get("linkToArchive") or (
                f"https://arquivo.pt/wayback/{timestamp}/{quote(str(original), safe=':/?=&%#')}"
            )
            captures.append(Capture(
                timestamp=timestamp,
                original_url=str(original),
                mimetype=mimetype,
                status_code=status,
                archive_digest=str(item.get("digest", "")),
                length=str(item.get("length", "")),
                capture_uri=str(replay_uri),
                archive_service=self.SERVICE,
            ))
        return captures[:limit]


ArchiveProviderFactory = Callable[[ArchiveConfig], WaybackCDXClient]
_ARCHIVE_PROVIDER_FACTORIES: dict[str, ArchiveProviderFactory] = {
    "wayback": WaybackCDXClient,
    "commoncrawl": CommonCrawlClient,
    "arquivo": ArquivoPTClient,
}


def register_archive_provider(name: str, factory: ArchiveProviderFactory) -> None:
    """Register a code-level adapter for another archive API.

    The adapter must implement `search_captures(...)` and `fetch_capture(...)`
    and return normalized `Capture` records.
    """
    normalized = name.strip().casefold()
    if not normalized or normalized in {"cdx", "wayback", "commoncrawl", "arquivo"}:
        raise ValueError("Choose a non-empty custom provider name")
    _ARCHIVE_PROVIDER_FACTORIES[normalized] = factory


def create_archive_client(config: ArchiveConfig | None = None) -> WaybackCDXClient:
    """Create the configured provider adapter.

    Built-ins are `wayback`, `commoncrawl`, and `arquivo`; `cdx` is for a
    generic CDX-compatible service. Other providers can be registered in code.
    """
    config = config or ArchiveConfig.from_environment()
    provider = config.provider.casefold()
    if provider == "cdx":
        if not config.api_base_url:
            raise ValueError("Set HDFR_ARCHIVE_API_BASE_URL for provider=cdx")
        if not config.capture_url_template:
            raise ValueError("Set HDFR_CAPTURE_URL_TEMPLATE for provider=cdx")
        return WaybackCDXClient(config)
    factory = _ARCHIVE_PROVIDER_FACTORIES.get(provider)
    if factory is None:
        available = ", ".join(sorted([*_ARCHIVE_PROVIDER_FACTORIES, "cdx"]))
        raise ValueError(f"Unsupported archive provider {provider!r}; available: {available}")
    return factory(config)


class _VisibleTextExtractor(HTMLParser):
    """Small standard-library HTML text extractor used when storing an artifact."""

    HIDDEN_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.HIDDEN_TAGS:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.HIDDEN_TAGS and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag.lower() in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)

    def get_text(self) -> str:
        return " ".join(" ".join(self.parts).split())


def extract_html_text(raw_html: bytes) -> str:
    """Extract visible text with stdlib; BeautifulSoup/Trafilatura can replace this."""
    text = raw_html.decode("utf-8", errors="replace")
    parser = _VisibleTextExtractor()
    parser.feed(text)
    return parser.get_text()


def _extract_warc_http_payload(raw_warc_gzip: bytes) -> bytes:
    """Return the HTTP entity body from one Common Crawl gzip WARC record."""
    try:
        warc = gzip.decompress(raw_warc_gzip)
        _, after_warc_headers = warc.split(b"\r\n\r\n", 1)
        http_headers, payload = after_warc_headers.split(b"\r\n\r\n", 1)
    except (OSError, ValueError) as exc:
        raise ArchiveAPIError("Could not decompress or parse Common Crawl WARC record") from exc
    for line in http_headers.split(b"\r\n"):
        if line.casefold().startswith(b"content-length:"):
            try:
                return payload[:int(line.split(b":", 1)[1].strip())]
            except ValueError:
                break
    return payload.rstrip(b"\r\n")


class SQLiteEvidenceStore:
    """Store artifact/event metadata in SQLite and payloads in a file directory."""

    def __init__(self, database_path: str | Path, artifact_directory: str | Path) -> None:
        self.database_path = Path(database_path)
        self.artifact_directory = Path(artifact_directory)
        self.artifact_directory.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        archive_service TEXT NOT NULL,
                        original_url TEXT NOT NULL,
                        capture_uri TEXT NOT NULL UNIQUE,
                        capture_timestamp TEXT NOT NULL,
                        mimetype TEXT NOT NULL,
                        status_code TEXT NOT NULL,
                        archive_digest TEXT,
                        provider_metadata_json TEXT NOT NULL DEFAULT '{}',
                        content_sha256 TEXT NOT NULL,
                        raw_path TEXT NOT NULL,
                        retrieved_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        event_id TEXT PRIMARY KEY,
                        artifact_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        event_time TEXT,
                        event_time_type TEXT NOT NULL,
                        confidence REAL,
                        evidence_json TEXT NOT NULL,
                        FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_artifacts_original_url
                        ON artifacts(original_url);
                    CREATE INDEX IF NOT EXISTS idx_artifacts_capture_timestamp
                        ON artifacts(capture_timestamp);
                    CREATE INDEX IF NOT EXISTS idx_events_event_time
                        ON events(event_time);
                    """
                )
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(artifacts)")
                }
                if "provider_metadata_json" not in columns:
                    connection.execute(
                        "ALTER TABLE artifacts ADD COLUMN provider_metadata_json TEXT NOT NULL DEFAULT '{}'"
                    )

    def save_artifact(self, capture: Capture, raw_content: bytes) -> dict[str, Any]:
        content_hash = sha256(raw_content).hexdigest()
        is_warc = capture.provider_data.get("format") == "commoncrawl-warc-gzip"
        extension = ".warc.gz" if is_warc else (".html" if "html" in capture.mimetype else ".bin")
        raw_path = self.artifact_directory / f"{content_hash}{extension}"
        if not raw_path.exists():
            raw_path.write_bytes(raw_content)
        html_payload = _extract_warc_http_payload(raw_content) if is_warc else raw_content
        artifact_id = sha256(capture.capture_uri.encode("utf-8")).hexdigest()
        retrieved_at = datetime.now(timezone.utc).isoformat()
        record = {
            "artifact_id": artifact_id,
            "archive_service": capture.archive_service,
            "original_url": capture.original_url,
            "capture_uri": capture.capture_uri,
            "capture_timestamp": capture.timestamp,
            "mimetype": capture.mimetype,
            "status_code": capture.status_code,
            "archive_digest": capture.archive_digest,
            "provider_metadata_json": json.dumps(capture.provider_data, sort_keys=True),
            "content_sha256": content_hash,
            "raw_path": str(raw_path),
            "retrieved_at": retrieved_at,
        }
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """INSERT INTO artifacts (
                        artifact_id, archive_service, original_url, capture_uri,
                        capture_timestamp, mimetype, status_code, archive_digest,
                        provider_metadata_json, content_sha256, raw_path, retrieved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(capture_uri) DO UPDATE SET
                        provider_metadata_json=excluded.provider_metadata_json,
                        content_sha256=excluded.content_sha256,
                        raw_path=excluded.raw_path,
                        retrieved_at=excluded.retrieved_at""",
                    tuple(record.values()),
                )
        record["extracted_text"] = extract_html_text(html_payload)
        return record

    def save_event(self, artifact_id: str, event_type: str, event_time: str | None,
                   event_time_type: str, confidence: float | None,
                   evidence: dict[str, Any]) -> str:
        """Persist an event linked to its supporting archive artifact."""
        event_id = str(uuid4())
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (event_id, artifact_id, event_type, event_time, event_time_type,
                     confidence, json.dumps(evidence, sort_keys=True, default=str)),
                )
        return event_id


class HDFRCollectionSystem:
    """Collect captures from Wayback, preserve payloads, and index provenance."""

    def __init__(self, database_path: str | Path = "data/hdfr.sqlite3",
                 artifact_directory: str | Path = "data/artifacts",
                 config: ArchiveConfig | None = None) -> None:
        self.client = create_archive_client(config)
        self.store = SQLiteEvidenceStore(database_path, artifact_directory)

    def collect_url(self, url: str, *, limit: int = 25,
                    from_year: int | None = None,
                    to_year: int | None = None) -> CollectionReport:
        """Discover and fetch URL captures; report per-capture errors and continue."""
        captures = self.client.search_captures(
            url, limit=limit, from_year=from_year, to_year=to_year)
        report = CollectionReport(discovered=len(captures))
        for capture in captures:
            try:
                raw = self.client.fetch_capture(capture)
                report.artifacts.append(self.store.save_artifact(capture, raw))
                report.saved += 1
            except (ArchiveAPIError, OSError, sqlite3.Error) as exc:
                report.errors.append({"capture_uri": capture.capture_uri, "error": str(exc)})
        return report


if __name__ == "__main__":
    # API base URL and API key may be left blank in .env.example.
    # Provide a URL to collect, for example:
    # python hdfr_system.py https://example.org --limit 10
    import argparse

    parser = argparse.ArgumentParser(description="Collect historical captures into HDFR storage")
    parser.add_argument("url", help="Original page URL to search in the configured archive")
    parser.add_argument("--limit", type=int, default=25, help="Maximum captures to fetch")
    parser.add_argument("--from-year", type=int)
    parser.add_argument("--to-year", type=int)
    parser.add_argument("--database", default="data/hdfr.sqlite3")
    parser.add_argument("--artifacts", default="data/artifacts")
    args = parser.parse_args()
    report = HDFRCollectionSystem(args.database, args.artifacts).collect_url(
        args.url, limit=args.limit, from_year=args.from_year, to_year=args.to_year)
    print(json.dumps({
        "discovered": report.discovered,
        "saved": report.saved,
        "errors": report.errors,
    }, indent=2))
