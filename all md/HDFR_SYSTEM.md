# HDFR Collection and System Layer

This module is deliberately separate from Algorithm 2. `hdfr_system.py` provides a provider-based collection pipeline that normalizes archive captures, downloads selected archived responses, extracts visible HTML text, and saves artifact/provenance metadata to SQLite.

## API configuration slot

The provider settings are kept out of the Streamlit UI. `hdfr_system.py` reads them from the project-root `.env` file; the blank template is [.env.example](../.env.example):

```dotenv
HDFR_ARCHIVE_PROVIDER=wayback
HDFR_ARCHIVE_API_BASE_URL=
HDFR_COMMONCRAWL_INDEX=
HDFR_ARCHIVE_API_KEY=
HDFR_CAPTURE_URL_TEMPLATE=
```

Copy `.env.example` to `.env`, then set `HDFR_ARCHIVE_PROVIDER` to `wayback`, `commoncrawl`, `arquivo`, or `cdx`. Leave the API base blank to use the selected provider's default. Wayback CDX and the public Common Crawl and Arquivo.pt endpoints do not normally require an API key. `HDFR_COMMONCRAWL_INDEX` can pin a crawl such as `CC-MAIN-2025-43`; leaving it blank selects the latest index. For a custom CDX-compatible service, set `HDFR_ARCHIVE_API_BASE_URL` and `HDFR_CAPTURE_URL_TEMPLATE`, using `{timestamp}` and `{original_url}` placeholders. Arbitrary JSON APIs need a provider adapter because their request and response formats vary.

## Run the UI

Install the UI dependencies and launch Streamlit from the project root:

```powershell
python -m pip install -r requirements-ui.txt
streamlit run app.py
```

The interface connects the collector and Algorithm 2. The Algorithm 1 and Algorithm 3 tabs are reserved and contain no implementation.
## Run a collection

No third-party Python package is required for the current Wayback collector.

```powershell
python hdfr_system.py https://example.org --limit 10 --from-year 2018 --to-year 2024
```

Default output locations are:

- `data/hdfr.sqlite3` — capture metadata, provenance, content hashes, and event records.
- `data/artifacts/` — raw response bodies, stored by SHA-256 content hash.

The collector queries the configured archive index for successful HTML captures, then downloads each capture using that provider's replay URL or WARC range. It reports captures discovered, captures saved, and per-capture errors. The default maximum is 25 captures per run; increase it deliberately and follow source access policies. This is historical capture retrieval, not a general-purpose crawler of the live web.

Programmatic use:

```python
from hdfr_system import HDFRCollectionSystem

system = HDFRCollectionSystem(
    database_path="data/hdfr.sqlite3",
    artifact_directory="data/artifacts",
)
report = system.collect_url(
    "https://example.org",
    limit=10,
    from_year=2018,
    to_year=2024,
)
print(report.discovered, report.saved, report.errors)
```

`report.artifacts` contains provenance references and a standard-library HTML text extraction. The database also has an `events` table for later temporal reconstruction; use `SQLiteEvidenceStore.save_event(...)` to save an event linked to an artifact. Extracted text is returned to the caller and is not stored as a duplicate of the raw payload.

## Suggested public/free archive APIs

These services provide public discovery or data access without a paid subscription for basic research use. “Free” does not mean unlimited: respect each service's terms, rate limits, robots/access policies, and availability. Coverage varies across archives, so combine sources where the research design requires broader coverage.

| API / source | Best use | Notes |
|---|---|---|
| [Internet Archive Wayback CDX API](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md) | Find captures by URL, date range, MIME type, and status; construct timestamped capture links | The public CDX endpoint is the default implemented by `hdfr_system.py`. The official documentation describes JSON output, filtering, limits, and pagination. |
| [Common Crawl Index Server](https://index.commoncrawl.org/) | Search URL records across selected Common Crawl snapshots and retrieve WARC record references | Common Crawl states its data and index files are free to download, while asking users not to overload its URL index. Large-scale work should use its bulk data guidance. |
| [Arquivo.pt APIs](https://arquivo.pt/api-docs/) | Search preserved Portuguese and international web content by text or URL; use its CDX/Memento-compatible access where suitable | Public API documentation is available online. Check current API docs and use limits before bulk collection. |

The collector currently includes adapters for Wayback CDX, Common Crawl CDXJ, and Arquivo.pt CDX/replay. Set `HDFR_ARCHIVE_PROVIDER` to select one. Common Crawl can use `HDFR_COMMONCRAWL_INDEX=CC-MAIN-2025-43` to pin a crawl; blank chooses the latest index. A custom CDX-compatible index uses `HDFR_ARCHIVE_PROVIDER=cdx`, plus its API base and capture URL template.

For another provider, implement a Python adapter with `search_captures(...)` and `fetch_capture(...)`, normalize results into the shared `Capture` dataclass, and register its factory with `register_archive_provider("provider_name", FactoryClass)`. Import that adapter in `app.py` before collection starts, then set `HDFR_ARCHIVE_PROVIDER=provider_name`. This makes new APIs pluggable without changing Algorithm 2. A URL alone cannot support every arbitrary API: each service's authentication, query parameters, response format, and payload retrieval need a compatible adapter.

## System components outside Algorithm 2

The broader system's proposed tools are documented in [HDFR_FRAMEWORK.md](../HDFR_FRAMEWORK.md). The current collection module uses Python's standard library for HTTP, HTML text extraction, hashing, and SQLite. BeautifulSoup or Trafilatura can replace the basic text extractor; Requests or HTTPX can replace the standard-library HTTP transport. Pandas, difflib/TF-IDF/Sentence Transformers, RapidFuzz, NetworkX, Plotly, and Streamlit belong to later preprocessing, change analysis, entity/relationship analysis, and visualization modules, not to Algorithm 2.

## Provenance fields captured

For each saved capture, the artifact table records archive service, original URL, capture URI, capture timestamp, MIME type, HTTP status, archive digest, locally computed SHA-256, local payload path, and retrieval time. Raw files remain outside SQLite; event rows can point back to their supporting artifacts. Capture timestamps identify when the archive recorded the page, not when a described historical event occurred.


