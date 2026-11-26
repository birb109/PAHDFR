"""Streamlit interface for the HDFR prototype.

Algorithm 1 and Algorithm 3 are intentionally reserved UI areas. This app does
not implement either algorithm.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import streamlit as st

from src.hdfr.collection.algorithm.algorithm_2 import EntityObservation, Provenance, correlate_archived_entities
from src.hdfr.app.hdfr_system import ArchiveConfig, ArchiveAPIError, HDFRCollectionSystem


# Private app configuration. Edit these defaults here, or set the corresponding
# environment variables; none of these settings are rendered in the UI.
PROJECT_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("HDFR_DATABASE_PATH", str(PROJECT_DIR / "data" / "hdfr.sqlite3")))
ARTIFACT_DIRECTORY = Path(os.getenv(
    "HDFR_ARTIFACT_DIRECTORY", str(PROJECT_DIR / "data" / "artifacts")
))
ARCHIVE_CONFIG = ArchiveConfig.from_environment()


st.set_page_config(
    page_title="HDFR | Historical Footprint Reconstruction",
    page_icon="◼",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      :root { --ink: #111111; --muted: #666666; --line: #dedede; --panel: #f6f6f6; }
      html, body, [data-testid="stAppViewContainer"] { background: #ffffff; color: var(--ink); }
      [data-testid="stHeader"] { background: rgba(255,255,255,0.92); }
      [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
      h1, h2, h3, p, label { color: var(--ink); }
      .hero { padding: 1.5rem 0 1.15rem; border-bottom: 1px solid var(--line); margin-bottom: 1.25rem; }
      .eyebrow { color: var(--muted); font-size: .78rem; letter-spacing: .13em; text-transform: uppercase; }
      .hero h1 { font-size: 2.15rem; line-height: 1.15; margin: .4rem 0; }
      .hero p { color: var(--muted); margin: 0; max-width: 850px; }
      .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; padding: 1.15rem; }
      .reserved { min-height: 300px; display: flex; flex-direction: column; justify-content: center; }
      .reserved h3 { margin-bottom: .4rem; }
      .reserved p { color: var(--muted); max-width: 680px; }
      div.stButton > button[kind="primary"] { background: #111111; border: 1px solid #111111; color: #ffffff; border-radius: 7px; }
      div.stButton > button[kind="secondary"] { border-color: #bdbdbd; color: #111111; border-radius: 7px; }
      [data-testid="stMetric"] { background: #f6f6f6; border: 1px solid var(--line); padding: .85rem; border-radius: 10px; }
      [data-baseweb="tab-list"] { gap: .4rem; }
      [data-baseweb="tab"] { color: #555555; }
      [aria-selected="true"][data-baseweb="tab"] { color: #111111; }
      code { color: #222222; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _parse_observations(text: str) -> list[EntityObservation]:
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("The input must be a JSON array of observations.")
    observations: list[EntityObservation] = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Each observation must be a JSON object.")
        provenance_data = row.get("provenance")
        if not isinstance(provenance_data, dict):
            raise ValueError(f"Observation {row.get('observation_id', '?')} needs a provenance object.")
        provenance = Provenance(
            archive_service=provenance_data.get("archive_service", ""),
            original_url=provenance_data.get("original_url", ""),
            capture_uri=provenance_data.get("capture_uri", ""),
            capture_timestamp=provenance_data.get("capture_timestamp"),
            artifact_id=provenance_data.get("artifact_id", ""),
            content_hash=provenance_data.get("content_hash"),
        )
        observations.append(EntityObservation(
            observation_id=row.get("observation_id", ""),
            entity_type=row.get("entity_type", ""),
            attributes=row.get("attributes", {}),
            provenance=provenance,
            event_time=row.get("event_time"),
            event_time_end=row.get("event_time_end"),
            extraction_confidence=row.get("extraction_confidence", 1.0),
        ))
    return observations


def _show_reserved(title: str, description: str) -> None:
    st.markdown(
        f"""
        <div class="panel reserved">
          <div class="eyebrow">Reserved workspace</div>
          <h3>{title}</h3>
          <p>{description}</p>
          <p><strong>Implementation status:</strong> not included in this UI build.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


st.markdown(
    """
    <div class="hero">
      <div class="eyebrow">PAHDFR · Research prototype</div>
      <h1>Historical Digital Footprint Reconstruction</h1>
      <p>Collect archived web evidence, inspect provenance, and correlate entity observations.
         Historical artifact discovery and timeline reconstruction have reserved workspaces.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

collection_tab, algorithm2_tab, algorithm1_tab, algorithm3_tab, evidence_tab = st.tabs([
    "Collection",
    "Algorithm 2 · Entity Correlation",
    "Algorithm 1 · Reserved",
    "Algorithm 3 · Reserved",
    "Evidence Store",
])

with collection_tab:
    st.subheader("Archive capture collection")
    st.write(
        f"Search the configured {ARCHIVE_CONFIG.provider} archive and download a limited set "
        "of archived HTML captures. Provider settings are loaded from the hidden `.env` configuration."
    )
    with st.form("collect_form"):
        target_url = st.text_input("Original URL", placeholder="https://example.org/about")
        c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
        limit = c1.number_input("Maximum captures", min_value=1, max_value=500, value=25, step=5)
        use_year_range = c2.checkbox("Filter by years", value=False)
        from_year = c3.number_input("From year", min_value=1990, max_value=2100, value=2018,
                                    disabled=not use_year_range)
        to_year = c4.number_input("To year", min_value=1990, max_value=2100, value=2024,
                                  disabled=not use_year_range)
        run_collection = st.form_submit_button("Discover and collect captures", type="primary")

    if run_collection:
        try:
            system = HDFRCollectionSystem(
                DATABASE_PATH, ARTIFACT_DIRECTORY, config=ARCHIVE_CONFIG
            )
            report = system.collect_url(
                target_url.strip(),
                limit=int(limit),
                from_year=int(from_year) if use_year_range else None,
                to_year=int(to_year) if use_year_range else None,
            )
            st.session_state["last_collection"] = {
                "discovered": report.discovered,
                "saved": report.saved,
                "artifacts": report.artifacts,
                "errors": report.errors,
            }
        except (ArchiveAPIError, ValueError, OSError, sqlite3.Error) as exc:
            st.error(f"Collection could not be completed: {exc}")

    report: dict[str, Any] | None = st.session_state.get("last_collection")
    if report:
        st.divider()
        a, b, c = st.columns(3)
        a.metric("Captures discovered", report["discovered"])
        b.metric("Artifacts saved", report["saved"])
        c.metric("Capture errors", len(report["errors"]))
        if report["artifacts"]:
            rows = [{
                "capture time": item["capture_timestamp"],
                "original URL": item["original_url"],
                "content SHA-256": item["content_sha256"],
                "artifact ID": item["artifact_id"],
                "saved file": item["raw_path"],
                "text preview": item.get("extracted_text", "")[:240],
            } for item in report["artifacts"]]
            st.dataframe(rows, use_container_width=True, hide_index=True)
            year_counts: dict[str, int] = {}
            for item in report["artifacts"]:
                year = str(item["capture_timestamp"])[:4]
                year_counts[year] = year_counts.get(year, 0) + 1
            try:
                import plotly.graph_objects as go
                figure = go.Figure(go.Bar(
                    x=list(year_counts), y=list(year_counts.values()),
                    marker_color="#333333",
                ))
                figure.update_layout(
                    title="Collected archive captures by capture year",
                    xaxis_title="Archive capture year", yaxis_title="Captures",
                    paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                    font_color="#111111", margin=dict(l=20, r=20, t=55, b=20),
                )
                st.plotly_chart(figure, use_container_width=True)
            except ImportError:
                st.info("Install Plotly to show the capture-year chart.")
        if report["errors"]:
            with st.expander("Capture errors"):
                st.dataframe(report["errors"], use_container_width=True, hide_index=True)

    with st.expander("Other public archive APIs"):
        st.markdown(
            "- [Internet Archive Wayback CDX](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md)\n"
            "- [Common Crawl Index Server](https://index.commoncrawl.org/)\n"
            "- [Arquivo.pt API documentation](https://arquivo.pt/api-docs/)\n\n"
            "Wayback, Common Crawl, Arquivo.pt, and CDX-compatible providers have adapters. "
            "Other APIs need a provider adapter for their specific response format."
        )

with algorithm2_tab:
    st.subheader("Correlate entity observations")
    st.caption("Paste observations extracted from preserved artifacts. This panel runs Algorithm 2 only.")
    observation_template = [
        {
            "observation_id": "obs-001",
            "entity_type": "organization",
            "attributes": {"name": "Example Research Group", "domain": "example.org"},
            "provenance": {
                "archive_service": "Internet Archive Wayback Machine",
                "original_url": "https://example.org/about",
                "capture_uri": "https://web.archive.org/web/20240101120000id_/https://example.org/about",
                "capture_timestamp": "2024-01-01T12:00:00Z",
                "artifact_id": "artifact-001",
                "content_hash": "sha256:replace-with-hash",
            },
            "event_time": None,
            "event_time_end": None,
            "extraction_confidence": 0.9,
        },
        {
            "observation_id": "obs-002",
            "entity_type": "organization",
            "attributes": {"name": "Example Research Group", "domain": "example.org"},
            "provenance": {
                "archive_service": "Internet Archive Wayback Machine",
                "original_url": "https://example.org/about",
                "capture_uri": "https://web.archive.org/web/20240201120000id_/https://example.org/about",
                "capture_timestamp": "2024-02-01T12:00:00Z",
                "artifact_id": "artifact-002",
            },
            "extraction_confidence": 0.9,
        },
    ]
    json_input = st.text_area(
        "Observation JSON", value=json.dumps(observation_template, indent=2), height=390,
    )
    left, right = st.columns([1, 3])
    threshold = left.number_input("Link threshold", min_value=0.0, max_value=1.0,
                                  value=0.72, step=0.01)
    if right.button("Run Algorithm 2", type="primary"):
        try:
            observations = _parse_observations(json_input)
            result = correlate_archived_entities(observations, threshold=float(threshold))
            st.session_state["last_correlation"] = {
                "clusters": result.clusters,
                "pair_evidence": result.pair_evidence,
                "errors": result.errors,
            }
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            st.error(f"Could not process observations: {exc}")
    correlation: dict[str, Any] | None = st.session_state.get("last_correlation")
    if correlation:
        st.markdown("#### Candidate entity clusters")
        st.json(correlation["clusters"])
        st.markdown("#### Pair-level evidence")
        if correlation["pair_evidence"]:
            st.dataframe(correlation["pair_evidence"], use_container_width=True, hide_index=True)
        else:
            st.info("No comparable observation pairs produced evidence.")
        if correlation["errors"]:
            with st.expander("Input and processing errors"):
                st.dataframe(correlation["errors"], use_container_width=True, hide_index=True)

with algorithm1_tab:
    _show_reserved(
        "Algorithm 1: Historical Artifact Discovery and Collection",
        "This area is reserved for the separate Algorithm 1 task. The current collection adapter above is a system utility; this panel does not define or implement Algorithm 1.",
    )

with algorithm3_tab:
    _show_reserved(
        "Algorithm 3: Evidence-Based Timeline Reconstruction",
        "This area is reserved for the separate Algorithm 3 task. No timeline-reconstruction logic is included here.",
    )

with evidence_tab:
    st.subheader("SQLite evidence store")
    st.caption("Browse captured artifacts and provenance metadata saved by the collection module.")
    if st.button("Refresh evidence store"):
        st.session_state["refresh_evidence"] = True
    db_file = DATABASE_PATH
    if not db_file.exists():
        st.info("No database yet. Collect a Wayback capture to create the evidence store.")
    else:
        try:
            with sqlite3.connect(db_file) as connection:
                artifact_count = connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
                event_count = connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                latest = connection.execute(
                    """SELECT capture_timestamp, original_url, archive_service,
                              content_sha256, raw_path
                       FROM artifacts ORDER BY capture_timestamp DESC LIMIT 500"""
                ).fetchall()
            x, y = st.columns(2)
            x.metric("Stored artifacts", artifact_count)
            y.metric("Stored events", event_count)
            st.dataframe([
                {"capture time": row[0], "original URL": row[1], "archive": row[2],
                 "SHA-256": row[3], "raw file": row[4]}
                for row in latest
            ], use_container_width=True, hide_index=True)
        except sqlite3.Error as exc:
            st.error(f"Could not read the evidence database: {exc}")

st.divider()
st.caption("PAHDFR prototype · white, black, and gray interface · only the collector and Algorithm 2 are connected")
