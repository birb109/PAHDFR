"""Streamlit UI that runs Algorithm 1, then Algorithm 2 over its stored artifacts.

Algorithm 1 implementation is imported and called unchanged. Algorithm 2 reads
and writes the same SQLite database that Algorithm 1 uses.
"""

from __future__ import annotations

import json
import html
import os
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime
from typing import Any

import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.hdfr.collection.algorithm.algorithm1 import HistoricalArtifactCollector
from src.hdfr.collection.algorithm.algorithm2_pipeline import Algorithm2Pipeline
from src.hdfr.storage import ArtifactStorage

DATABASE_PATH = Path(os.getenv("HDFR_DATABASE_PATH", str(PROJECT_ROOT / "data" / "hdfr.db")))
ARTIFACT_DIRECTORY = Path(os.getenv(
    "HDFR_ARTIFACT_DIRECTORY", str(PROJECT_ROOT / "data" / "artifacts")
))

st.set_page_config(
    page_title="PAHDFR | Historical Footprint Reconstruction",
    page_icon="◼",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BOOTSTRAP_CSS_PATH = PROJECT_ROOT / "node_modules" / "bootstrap" / "dist" / "css" / "bootstrap.min.css"
STYLESHEET_PATH = Path(__file__).with_name("styles.css")
HTML_LAYOUT_PATH = Path(__file__).with_name("layout.html")
st.markdown(
    "<style>"
    + BOOTSTRAP_CSS_PATH.read_text(encoding="utf-8")
    + "\n"
    + STYLESHEET_PATH.read_text(encoding="utf-8")
    + "</style>",
    unsafe_allow_html=True,
)


def _html_fragment(fragment_name: str, **values: str) -> str:
    """Load a named HTML template fragment and safely fill its text values."""
    layout = HTML_LAYOUT_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf'<template\s+id="{re.escape(fragment_name)}">(.*?)</template>',
        layout,
        flags=re.DOTALL,
    )
    if match is None:
        raise ValueError(f"HTML fragment {fragment_name!r} is missing from {HTML_LAYOUT_PATH}")
    fragment = match.group(1).strip()
    for key, value in values.items():
        fragment = fragment.replace("{{ " + key + " }}", html.escape(value))
    return fragment


def _annotation_template(artifacts: list[dict[str, Any]], selected_ids: list[str]) -> str:
    """Make annotation rows keyed to real Algorithm 1 artifact IDs."""
    return json.dumps([
        {"artifact_id": artifact_id, "entities": []}
        for artifact_id in selected_ids
    ], indent=2, ensure_ascii=False)


def _artifact_label(artifact: dict[str, Any]) -> str:
    return (
        f"{artifact['artifact_id'][:12]} · {artifact.get('source', 'unknown')} · "
        f"{artifact.get('source_uri', '')} · {artifact.get('capture_time') or 'date unknown'}"
    )


def _latest_collection_artifact_ids() -> set[str] | None:
    """Get successful artifacts produced by the most recently saved Algorithm 1 run."""
    if not DATABASE_PATH.exists():
        return None
    with sqlite3.connect(DATABASE_PATH) as connection:
        latest = connection.execute(
            "SELECT started_at FROM collection_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return None
        rows = connection.execute(
            "SELECT artifact_id FROM artifacts "
            "WHERE collection_status = 'success' AND collection_time >= ?",
            (latest[0],),
        ).fetchall()
    return {str(row[0]) for row in rows}


with st.container():
    st.markdown(_html_fragment("hero"), unsafe_allow_html=True)

collection_tab, algorithm2_tab, artifact_tab, algorithm3_tab = st.tabs([
    "Algorithm 1 · Collect artifacts",
    "Algorithm 2 · Correlate entities",
    "Algorithm 1 · Stored artifacts",
    "Algorithm 3 · Reserved",
])

with collection_tab:
    st.subheader("Algorithm 1: Historical Artifact Discovery and Collection")
    st.caption(f"Algorithm 1 storage: `{DATABASE_PATH}`")
    with st.form("algorithm1_collection_form"):
        target_url = st.text_input("Target URL", placeholder="https://example.org/about")
        sources = st.multiselect(
            "Archive sources",
            options=["wayback", "common_crawl"],
            default=["wayback", "common_crawl"],
        )
        col1, col2, col3 = st.columns(3)
        from_date = col1.text_input(
            "Wayback from date (YYYYMMDD, optional)",
            placeholder="20200101",
            help="Enter exactly 8 digits, for example 20200101. Leave blank for no lower date limit.",
        )
        to_date = col2.text_input(
            "Wayback to date (YYYYMMDD, optional)",
            placeholder="20251231",
            help="Enter exactly 8 digits, for example 20251231. Leave blank for no upper date limit.",
        )
        common_crawl_index = col3.text_input(
            "Common Crawl index", value="CC-MAIN-2025-30"
        )
        crawl_site = st.checkbox(
            "Discover and crawl same-domain links",
            value=False,
            help=(
                "Reads public HTML pages only to find internal links, stays on the starting host, "
                "respects robots.txt, and searches the selected archives for each discovered URL. "
                "Live page content is not stored."
            ),
        )
        crawl_col1, crawl_col2 = st.columns(2)
        max_crawl_pages = crawl_col1.number_input(
            "Maximum pages to discover",
            min_value=1,
            max_value=100,
            value=25,
            step=1,
            disabled=not crawl_site,
            help="Includes the starting URL. Use a small limit for initial tests.",
        )
        crawl_depth = crawl_col2.number_input(
            "Link depth",
            min_value=0,
            max_value=5,
            value=5,
            step=1,
            disabled=not crawl_site,
            help="Depth 0 searches only the starting page; depth 1 also searches its direct links.",
        )
        run_algorithm1 = st.form_submit_button("Run Algorithm 1", type="primary")

    if run_algorithm1:
        st.session_state.pop("algorithm1_result", None)
        st.session_state.pop("algorithm2_result", None)
        st.session_state["algorithm2_selected_artifacts"] = []
        st.session_state.pop("algorithm2_annotation_json", None)
        st.session_state.pop("algorithm2_extraction_errors", None)
        collector: HistoricalArtifactCollector | None = None
        try:
            from_value = from_date.strip() or None
            to_value = to_date.strip() or None
            for field_name, date_value in (("from date", from_value), ("to date", to_value)):
                if date_value is not None:
                    if not re.fullmatch(r"\d{8}", date_value):
                        raise ValueError(
                            f"Wayback {field_name} must be exactly 8 digits in YYYYMMDD format "
                            "(for example, 20200101)."
                        )
                    try:
                        datetime.strptime(date_value, "%Y%m%d")
                    except ValueError as exc:
                        raise ValueError(
                            f"Wayback {field_name} is not a valid calendar date. "
                            "Use YYYYMMDD, for example 20200101."
                        ) from exc
            if from_value and to_value and from_value > to_value:
                raise ValueError("Wayback from date must be on or before the to date.")
            collector = HistoricalArtifactCollector(
                storage_dir=str(ARTIFACT_DIRECTORY),
                database_path=str(DATABASE_PATH),
            )
            loading_text = (
                f"Discovering up to {int(max_crawl_pages)} same-host pages and checking archive captures..."
                if crawl_site else "Checking archive captures for the target URL..."
            )
            with st.spinner(loading_text):
                result = collector.run(
                    target_url=target_url.strip(),
                    sources=sources,
                    from_date=from_value,
                    to_date=to_value,
                    common_crawl_index=common_crawl_index.strip() or "CC-MAIN-2025-30",
                    crawl_site=crawl_site,
                    max_crawl_pages=int(max_crawl_pages),
                    crawl_depth=int(crawl_depth),
                )
            st.session_state["algorithm1_result"] = result
            success_message = (
                f"Algorithm 1 finished: {result['collected_count']} artifact(s) stored "
                f"from {result['discovered_count']} discovered."
            )
            with st.container():
                st.markdown(
                    _html_fragment("collection-success", message=success_message),
                    unsafe_allow_html=True,
                )
        except (ValueError, OSError, sqlite3.Error) as exc:
            st.error(f"Algorithm 1 could not complete: {exc}")
        finally:
            if collector is not None:
                collector.session.close()

    collection_result: dict[str, Any] | None = st.session_state.get("algorithm1_result")
    if collection_result:
        st.divider()
        if collection_result.get("crawl_enabled"):
            st.caption(
                f"Link discovery found {collection_result.get('crawl_page_count', 1)} same-domain "
                "page URL(s). Counts below represent archived captures of those URLs, not a count "
                "of books or other entities. Identical Wayback content is collapsed during discovery."
            )
        else:
            st.caption(
                "Counts represent archived captures of the exact URL entered, not the number of "
                "books or pages on that site. Enable same-domain link discovery to search linked "
                "pages. Identical Wayback content is collapsed during discovery."
            )
        m1, m2, m3 = st.columns(3)
        m1.metric("Artifacts discovered", collection_result["discovered_count"])
        m2.metric("Artifacts stored", collection_result["collected_count"])
        m3.metric("Collection errors", collection_result["failed_count"])
        if collection_result.get("artifacts"):
            st.dataframe([{
                "artifact_id": item.get("artifact_id"),
                "source": item.get("source"),
                "source_uri": item.get("source_uri"),
                "capture_time": item.get("capture_time"),
                "content_hash": item.get("content_hash"),
                "storage_path": item.get("storage_path"),
            } for item in collection_result["artifacts"]], use_container_width=True, hide_index=True)
        if collection_result.get("errors"):
            with st.expander("Algorithm 1 errors"):
                st.dataframe(collection_result["errors"], use_container_width=True, hide_index=True)

with algorithm2_tab:
    st.subheader("Algorithm 2: Temporal-Aware Entity Correlation")
    st.write(
        "Algorithm 2 reads artifact IDs, source URLs, archive URIs, capture times, and content "
        "hashes directly from Algorithm 1's SQLite `artifacts` table. Use the extraction button "
        "to populate entity candidates from archived HTML, review the JSON annotations, then run "
        "correlation. Algorithm 1 preserves the artifacts but does not itself extract entities."
    )
    pipeline = Algorithm2Pipeline(DATABASE_PATH)
    all_available = pipeline.available_artifacts()
    latest_artifact_ids = _latest_collection_artifact_ids()
    available = (
        [row for row in all_available if str(row["artifact_id"]) in latest_artifact_ids]
        if latest_artifact_ids is not None else all_available
    )
    if not available:
        if all_available and latest_artifact_ids is not None:
            st.info(
                "The latest Algorithm 1 run did not store artifacts. Older artifacts remain in "
                "the database for provenance, but are cleared from this correlation selector."
            )
        else:
            st.info("No successful Algorithm 1 artifacts are stored yet. Run Algorithm 1 first.")
    else:
        if latest_artifact_ids is not None:
            st.caption(
                "This selector shows artifacts from the latest Algorithm 1 run. Earlier artifacts "
                "remain in the stored-artifacts view for provenance."
            )
        id_to_artifact = {str(row["artifact_id"]): row for row in available}
        selected_ids = st.multiselect(
            "Select Algorithm 1 artifacts to correlate",
            options=list(id_to_artifact),
            format_func=lambda artifact_id: _artifact_label(id_to_artifact[artifact_id]),
            default=[],
            key="algorithm2_selected_artifacts",
        )
        st.caption(
            "Algorithm 2 can extract candidate entities from the saved HTML using structured "
            "metadata and headings. Review the candidates before correlation; this lightweight "
            "extractor may miss entities mentioned only in page prose."
        )
        for artifact_id in selected_ids:
            with st.expander(f"Review stored page content · {artifact_id[:16]}"):
                try:
                    st.text(pipeline.artifact_text_preview(artifact_id))
                except (OSError, KeyError, ValueError) as exc:
                    st.warning(f"Could not read this Algorithm 1 payload: {exc}")
        template = _annotation_template(available, selected_ids)
        if st.button(
            "Extract entities from selected artifacts",
            disabled=not selected_ids,
            key="algorithm2_extract_entities",
        ):
            with st.spinner("Extracting entity candidates from saved archived HTML..."):
                extracted, extraction_errors = pipeline.extract_entity_annotations(selected_ids)
            st.session_state["algorithm2_annotation_json"] = json.dumps(
                extracted, indent=2, ensure_ascii=False,
            )
            st.session_state["algorithm2_extraction_errors"] = extraction_errors
            candidate_count = sum(len(item["entities"]) for item in extracted)
            st.success(f"Extracted {candidate_count} entity candidate(s) from {len(selected_ids)} artifact(s).")
            if candidate_count == 0:
                st.info(
                    "No candidates were found in the supported HTML metadata or headings. "
                    "You can still add entities manually in the JSON editor below."
                )
        extraction_errors = st.session_state.get("algorithm2_extraction_errors", [])
        if extraction_errors:
            with st.expander("Entity extraction notes"):
                st.dataframe(extraction_errors, use_container_width=True, hide_index=True)
        annotation_json = st.text_area(
            "Entity annotations (JSON)",
            value=template,
            height=300,
            key="algorithm2_annotation_json",
            help=(
                'Each item: {"artifact_id":"...","entities":[{"entity_type":"organization",'
                '"attributes":{"name":"..."},"event_time":null,"extraction_confidence":0.9}]}'
            ),
        )
        col1, col2 = st.columns([1, 3])
        threshold = col1.number_input(
            "Correlation threshold", min_value=0.0, max_value=1.0,
            value=0.72, step=0.01,
        )
        with col2.container(key="algorithm2_run_button"):
            run_algorithm2 = st.button(
                "Run Algorithm 2 on Algorithm 1 data",
                type="primary",
                disabled=not selected_ids,
            )
        if run_algorithm2:
            try:
                annotations = json.loads(annotation_json)
                if not isinstance(annotations, list):
                    raise ValueError("Entity annotations must be a JSON array.")
                annotation_artifact_ids = {
                    str(item.get("artifact_id", ""))
                    for item in annotations if isinstance(item, dict)
                }
                unknown_selected = annotation_artifact_ids - set(selected_ids)
                if unknown_selected:
                    raise ValueError(
                        "Annotations must reference only the selected Algorithm 1 artifacts: "
                        + ", ".join(sorted(unknown_selected))
                    )
                run = pipeline.run(annotations, threshold=float(threshold))
                st.session_state["algorithm2_result"] = {
                    "run_id": run.run_id,
                    "artifact_count": len(selected_ids),
                    "observation_count": run.observation_count,
                    "clusters": run.result.clusters,
                    "pair_evidence": run.result.pair_evidence,
                    "errors": run.result.errors,
                }
            except (ValueError, TypeError, sqlite3.Error) as exc:
                st.error(f"Algorithm 2 could not complete: {exc}")

    algorithm2_result: dict[str, Any] | None = st.session_state.get("algorithm2_result")
    if algorithm2_result:
        st.divider()
        st.caption(f"Saved Algorithm 2 run: `{algorithm2_result['run_id']}` in `{DATABASE_PATH}`")
        a, b = st.columns(2)
        a.metric("Selected source artifacts", algorithm2_result["artifact_count"])
        b.metric("Entity observations", algorithm2_result["observation_count"])
        if algorithm2_result["observation_count"] == 0:
            st.info(
                "No entity observations were provided. Add one or more entity objects to the "
                "`entities` array for each selected artifact; selecting artifacts alone does not "
                "create observations."
            )
        st.markdown("#### Candidate entity clusters")
        st.json(algorithm2_result["clusters"])
        st.markdown("#### Pair-level evidence")
        if algorithm2_result["pair_evidence"]:
            st.dataframe(algorithm2_result["pair_evidence"], use_container_width=True, hide_index=True)
        else:
            st.info("No comparable entity pairs were available in this run.")
        if algorithm2_result["errors"]:
            with st.expander("Annotation and processing errors"):
                st.dataframe(algorithm2_result["errors"], use_container_width=True, hide_index=True)

    try:
        recent_runs = pipeline.recent_runs()
        if recent_runs:
            with st.expander("Recent Algorithm 2 runs saved in the Algorithm 1 database"):
                st.dataframe(recent_runs, use_container_width=True, hide_index=True)
    except sqlite3.Error as exc:
        st.warning(f"Could not read Algorithm 2 run history: {exc}")

with artifact_tab:
    st.subheader("Artifacts stored by Algorithm 1")
    st.caption(f"Shared Algorithm 1 / Algorithm 2 database: `{DATABASE_PATH}`")
    st.caption("This table lists every successfully stored artifact; there is no three-artifact display limit.")
    try:
        stored_artifacts = Algorithm2Pipeline(DATABASE_PATH).available_artifacts()
        if not stored_artifacts:
            st.info("No successful Algorithm 1 artifacts are stored yet.")
        else:
            st.metric("Available artifacts", len(stored_artifacts))
            st.dataframe([{
                "artifact_id": row.get("artifact_id"),
                "source": row.get("source"),
                "source_uri": row.get("source_uri"),
                "archive_uri": row.get("archive_uri"),
                "capture_time": row.get("capture_time"),
                "content_hash": row.get("content_hash"),
                "storage_path": row.get("storage_path"),
            } for row in stored_artifacts], use_container_width=True, hide_index=True)
    except sqlite3.Error as exc:
        st.error(f"Could not read Algorithm 1 storage: {exc}")

with algorithm3_tab:
    with st.container():
        st.markdown(_html_fragment("algorithm3-placeholder"), unsafe_allow_html=True)

st.divider()
st.caption(
    "Algorithm 1 is imported unchanged. Algorithm 2 consumes its stored artifacts and persists "
    "correlation runs to the same SQLite database."
)
