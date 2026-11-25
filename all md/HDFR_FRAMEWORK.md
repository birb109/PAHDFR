# HDFR Methodology, Architecture, and Technology Stack

## Project

**Developing a Framework for Historical Digital Footprint Reconstruction Using Open-Source Intelligence (HDFR)** integrates historical artifact discovery and preservation, content analysis, entity correlation, temporal reconstruction, provenance tracking, and reproducible evaluation.

This document describes the broader HDFR system. The tools below belong to the system methodology and architecture; they are not additions to Algorithm 2. Algorithm 2 remains the entity-correlation procedure described in [ALGORITHM2.md](all%20md/ALGORITHM2.md). A separate collection implementation and archive API configuration guide is in [HDFR_SYSTEM.md](all%20md/HDFR_SYSTEM.md), with code in [hdfr_system.py](hdfr_system.py).

## Methodology

The methodology follows the three phases and ten activities in the supplied [Draw.io methodology reference](figures/HDFR%20Methodology%20Reference.drawio).

### Phase 1: Evidence acquisition

1. **Historical source discovery** — identify relevant web archives, public datasets, and other lawful OSINT sources.
2. **Artifact collection** — retrieve pages and metadata from supported archive APIs or permitted web endpoints. Record source, URL, capture identifier, and retrieval time.
3. **Data preservation** — retain the raw response or archived artifact, compute a content hash, and attach provenance metadata so later findings can be traced to evidence.

### Phase 2: Evidence processing and reconstruction

4. **Content and metadata preprocessing** — extract page text and metadata, normalize timestamps and URLs, standardize records, and identify duplicates while preserving links to source artifacts.
5. **Change detection** — compare captures to distinguish substantive content changes from formatting or boilerplate differences. Keep the comparison method and parameters with the result.
6. **Historical entity correlation** — identify and match entity mentions across captures and sources using attributes, relationships, temporal evidence, and confidence/uncertainty. Algorithm 2 is one component of this activity.
7. **Temporal reconstruction** — assemble historical versions and candidate timelines. Keep capture, publication, modification, and event times distinct; retain conflicting or uncertain date evidence instead of silently selecting one date.
8. **Evidence validation** — inspect provenance and supporting artifacts, resolve or flag conflicts, and preserve the evidence behind each reconstructed finding.

### Phase 3: Experimental evaluation

9. **Performance evaluation** — evaluate against a benchmark with ground-truth annotations using task-appropriate metrics, such as precision, recall, and F1 for entity matching and date accuracy or interval overlap for temporal reconstruction.
10. **Comparative analysis** — compare baseline methods and run ablations that remove or vary provenance management, temporal modeling, and entity resolution. Record dataset version, code version, and parameters for reproducibility.

## System architecture

The supplied [HDFR System Architecture figure](figures/HDFR%20System%20Architecture%20Reference.png) is the visual reference for these layers.

![HDFR System Architecture reference](figures/HDFR%20System%20Architecture%20Reference.png)

| Layer | Responsibilities | Proposed tools/components |
|---|---|---|
| Data sources | Wayback Machine, Common Crawl, open web archives, public datasets, and other lawful sources | Source-specific APIs and documented access policies |
| Collection | Crawl permitted pages, call archive APIs, ingest responses, and normalize records | Python orchestration; Requests or HTTPX for HTTP/API retrieval |
| Storage and provenance | Preserve raw artifacts and standardized metadata with evidence links | SQLite for artifact/event/provenance metadata; filesystem or object storage for raw HTML/WARC and other large payloads; content hashes; provenance relationships compatible with W3C PROV-O concepts |
| Analysis and entity correlation | Extract entities, deduplicate, compare versions, correlate identities and relationships, and analyze time series | BeautifulSoup or Trafilatura; Pandas; difflib, TF-IDF, or Sentence Transformers; RapidFuzz; NetworkX |
| Temporal reconstruction | Assemble historical versions and events; retain date types, conflicts, confidence, and uncertainty | Python date/time handling and explicit temporal evidence records; versioned outputs |
| Visualization and reporting | Explore timelines and graphs, produce reports, and export results | Plotly for interactive charts; Streamlit for a dashboard; JSON/PDF export as required |
| Evaluation | Compare systems and ablations using a ground-truth benchmark | Versioned evaluation scripts, metric reports, and stored run configuration |

## Technology stack and component roles

- **Python** — coordinates collection, processing, correlation, reconstruction, evaluation, and reporting.
- **Requests or HTTPX** — retrieves data through supported APIs and permitted HTTP endpoints. Respect source terms, rate limits, and access restrictions.
- **BeautifulSoup or Trafilatura** — parses HTML and extracts page content and metadata. Select based on whether flexible DOM inspection or article-focused extraction is needed.
- **Pandas** — cleans, standardizes, joins, and analyzes tabular metadata and evaluation results.
- **difflib, TF-IDF, or Sentence Transformers** — alternative levels of content comparison, from basic sequence comparison to lexical or semantic similarity. The selected method and configuration should be recorded per run.
- **RapidFuzz and NetworkX** — fuzzy entity matching and relationship graph analysis. Match scores should remain evidence for review rather than being treated as certainty by themselves.
- **SQLite** — stores artifact metadata, events, entity observations, provenance references, correlation evidence, and run parameters. Keep large raw artifacts in file or object storage and reference them by URI and hash.
- **Plotly and Streamlit** — Plotly renders interactive timelines and graphs; Streamlit can provide the user-facing exploration dashboard.

These are proposed implementation choices, not a claim that every package or system component is already implemented in this repository. Alternatives separated by “or” are choices to be selected and evaluated, not dependencies that must all be installed.

## Provenance and data flow

Every derived entity, change, event, and timeline item should reference one or more source artifacts. At minimum, record the archive/source name, original URL, capture URI or identifier, capture timestamp, retrieval timestamp where applicable, content hash, extraction method/version, and transformation/run identifier. Store all relevant timestamp types separately and include the evidence source and confidence for inferred dates. Never substitute a capture timestamp for an event date without explicitly labeling that inference.

SQLite is the metadata and relationship store in this design. The raw response bodies and archived page payloads remain in durable file/object storage, with their paths and hashes indexed in SQLite. This keeps evidence traceable while avoiding oversized database blobs.

## Relationship to research contributions

- **C1 — Provenance-Aware Historical Reconstruction:** acquisition, preservation, standardized metadata, artifact hashes, and links from findings back to original evidence.
- **C2 — Uncertainty-Aware Temporal and Entity Correlation:** content/time evidence, entity matching, relationship analysis, date-type separation, confidence, and explicit conflict/uncertainty handling.
- **C3 — Reproducible Experimental Evaluation:** benchmark annotations, baseline comparisons, ablation studies, versioned parameters, and repeatable evaluation outputs.

The reference figures communicate workflow and architecture. Their labels are treated as design references; the implementation choices and responsibilities are specified here.


