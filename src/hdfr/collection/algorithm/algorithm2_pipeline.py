"""Algorithm 2 integration over Algorithm 1's artifact database.

This module leaves Algorithm 1 unchanged. It reads successful artifacts from
ArtifactStorage, attaches entity annotations to those artifact IDs, invokes the
existing Algorithm 2 correlator, and stores its run/evidence tables in the same
SQLite database file.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
import json
from html.parser import HTMLParser
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping
from uuid import uuid4

from src.hdfr.collection.algorithm.algorithm_2 import (
    CorrelationResult,
    EntityObservation,
    Provenance,
    correlate_archived_entities,
)
from src.hdfr.storage import ArtifactStorage


@dataclass
class Algorithm2Run:
    run_id: str
    artifact_count: int
    observation_count: int
    result: CorrelationResult


class Algorithm2Pipeline:
    """Run entity correlation on entities annotated against Algorithm 1 artifacts."""

    def __init__(self, database_path: str | Path = "data/hdfr.db") -> None:
        self.database_path = Path(database_path)
        # Uses Algorithm 1's own storage facade and the exact same database file.
        self.storage = ArtifactStorage(str(self.database_path))
        self._initialize_algorithm2_tables()

    def available_artifacts(self) -> list[dict[str, Any]]:
        """Return successfully collected Algorithm 1 artifacts for annotation."""
        return [
            item for item in self.storage.get_artifacts()
            if item.get("collection_status") == "success"
        ]

    def artifact_text_preview(self, artifact_id: str, max_chars: int = 5000) -> str:
        """Read the stored Algorithm 1 payload to support entity annotation."""
        artifact = next(
            (row for row in self.available_artifacts()
             if str(row.get("artifact_id")) == artifact_id),
            None,
        )
        if artifact is None:
            raise KeyError(f"Algorithm 1 artifact not found: {artifact_id}")
        path_value = artifact.get("storage_path")
        if not path_value:
            return "No stored payload path is recorded for this artifact."
        path = Path(path_value)
        if not path.is_file():
            return f"Stored artifact payload is missing: {path}"
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        if "html" not in str(artifact.get("mime_type") or "").casefold():
            return text[:max_chars]

        class VisibleText(HTMLParser):
            hidden = {"script", "style", "noscript", "svg"}

            def __init__(self) -> None:
                super().__init__(convert_charrefs=True)
                self.parts: list[str] = []
                self.hidden_depth = 0

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag.casefold() in self.hidden:
                    self.hidden_depth += 1

            def handle_endtag(self, tag: str) -> None:
                if tag.casefold() in self.hidden and self.hidden_depth:
                    self.hidden_depth -= 1
                elif tag.casefold() in {"p", "div", "br", "li", "h1", "h2", "h3"}:
                    self.parts.append(" ")

            def handle_data(self, data: str) -> None:
                if not self.hidden_depth:
                    cleaned = " ".join(data.split())
                    if cleaned:
                        self.parts.append(cleaned)

        parser = VisibleText()
        parser.feed(text)
        return " ".join(" ".join(parser.parts).split())[:max_chars]

    @staticmethod
    def _capture_time(value: Any) -> str | None:
        if value is None or value == "":
            return None
        text = str(value).strip()
        if re.fullmatch(r"\d{14}", text):
            parsed = datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        if re.fullmatch(r"\d{8}", text):
            parsed = datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        # Let Algorithm 2 validate the ISO timestamp and retain its error details.
        return text

    def build_observations(
        self,
        entity_annotations: Iterable[Mapping[str, Any]],
    ) -> tuple[list[EntityObservation], dict[str, str], list[dict[str, str]]]:
        """Create Algorithm 2 observations linked to existing Algorithm 1 rows.

        Annotation format is one object per artifact:
        {"artifact_id": "...", "entities": [{"entity_type": "...",
         "attributes": {"name": "..."}, "event_time": null,
         "extraction_confidence": 0.9}]}
        """
        artifacts = {str(row["artifact_id"]): row for row in self.available_artifacts()}
        observations: list[EntityObservation] = []
        observation_artifact: dict[str, str] = {}
        errors: list[dict[str, str]] = []
        seen_observations: set[str] = set()

        for annotation in entity_annotations:
            if not isinstance(annotation, Mapping):
                errors.append({"artifact_id": "", "error": "annotation entry must be an object"})
                continue
            artifact_id = str(annotation.get("artifact_id", "")).strip()
            artifact = artifacts.get(artifact_id)
            if artifact is None:
                errors.append({
                    "artifact_id": artifact_id,
                    "error": "artifact_id was not found among successful Algorithm 1 artifacts",
                })
                continue
            entities = annotation.get("entities", [])
            if not isinstance(entities, list):
                errors.append({"artifact_id": artifact_id, "error": "entities must be a list"})
                continue

            for index, entity in enumerate(entities, start=1):
                if not isinstance(entity, Mapping):
                    errors.append({"artifact_id": artifact_id, "error": "entity annotation must be an object"})
                    continue
                entity_type = str(entity.get("entity_type", "")).strip()
                attributes = entity.get("attributes", {})
                if not entity_type or not isinstance(attributes, Mapping):
                    errors.append({
                        "artifact_id": artifact_id,
                        "error": "each entity needs entity_type and an attributes object",
                    })
                    continue
                observation_id = str(entity.get(
                    "observation_id", f"{artifact_id}:entity-{index}"
                )).strip()
                if not observation_id or observation_id in seen_observations:
                    errors.append({
                        "artifact_id": artifact_id,
                        "error": f"empty or duplicate observation_id: {observation_id!r}",
                    })
                    continue

                source_uri = str(artifact.get("source_uri") or "")
                archive_uri = str(artifact.get("archive_uri") or "")
                confidence = entity.get("extraction_confidence")
                if confidence is None:
                    confidence = artifact.get("evidence_confidence")
                if confidence is None:
                    confidence = 1.0
                provenance = Provenance(
                    archive_service=str(artifact.get("source") or "unknown"),
                    original_url=source_uri,
                    capture_uri=archive_uri,
                    capture_timestamp=self._capture_time(artifact.get("capture_time")),
                    artifact_id=artifact_id,
                    content_hash=artifact.get("content_hash"),
                )
                observation = EntityObservation(
                    observation_id=observation_id,
                    entity_type=entity_type,
                    attributes=dict(attributes),
                    provenance=provenance,
                    event_time=entity.get("event_time", artifact.get("event_time")),
                    event_time_end=entity.get("event_time_end"),
                    extraction_confidence=confidence,
                )
                observations.append(observation)
                observation_artifact[observation_id] = artifact_id
                seen_observations.add(observation_id)

        return observations, observation_artifact, errors

    def run(
        self,
        entity_annotations: Iterable[Mapping[str, Any]],
        *,
        threshold: float = 0.72,
        half_life_days: float = 365.0,
        attribute_weights: Mapping[str, float] | None = None,
    ) -> Algorithm2Run:
        observations, observation_artifact, annotation_errors = self.build_observations(
            entity_annotations
        )
        result = correlate_archived_entities(
            observations,
            threshold=threshold,
            half_life_days=half_life_days,
            attribute_weights=attribute_weights,
        )
        result.errors[:0] = annotation_errors
        run_id = str(uuid4())
        self._save_run(
            run_id, observations, observation_artifact, result,
            threshold, half_life_days, attribute_weights,
        )
        artifact_ids = set(observation_artifact.values())
        return Algorithm2Run(
            run_id=run_id,
            artifact_count=len(artifact_ids),
            observation_count=len(observations),
            result=result,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_algorithm2_tables(self) -> None:
        with closing(self._connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS algorithm2_runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    threshold REAL NOT NULL,
                    half_life_days REAL NOT NULL,
                    attribute_weights_json TEXT NOT NULL,
                    artifact_count INTEGER NOT NULL,
                    observation_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS algorithm2_observations (
                    run_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    event_time TEXT,
                    event_time_end TEXT,
                    extraction_confidence REAL NOT NULL,
                    PRIMARY KEY (run_id, observation_id),
                    FOREIGN KEY (run_id) REFERENCES algorithm2_runs(run_id),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS algorithm2_pair_evidence (
                    run_id TEXT NOT NULL,
                    observation_a TEXT NOT NULL,
                    observation_b TEXT NOT NULL,
                    score REAL NOT NULL,
                    decision TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, observation_a, observation_b),
                    FOREIGN KEY (run_id, observation_a)
                        REFERENCES algorithm2_observations(run_id, observation_id),
                    FOREIGN KEY (run_id, observation_b)
                        REFERENCES algorithm2_observations(run_id, observation_id)
                );
                CREATE TABLE IF NOT EXISTS algorithm2_cluster_members (
                    run_id TEXT NOT NULL,
                    cluster_id TEXT NOT NULL,
                    observation_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    PRIMARY KEY (run_id, cluster_id, observation_id),
                    FOREIGN KEY (run_id, observation_id)
                        REFERENCES algorithm2_observations(run_id, observation_id),
                    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
                );
                CREATE TABLE IF NOT EXISTS algorithm2_errors (
                    run_id TEXT NOT NULL,
                    error_index INTEGER NOT NULL,
                    observation_id TEXT,
                    error TEXT NOT NULL,
                    PRIMARY KEY (run_id, error_index),
                    FOREIGN KEY (run_id) REFERENCES algorithm2_runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS idx_algorithm2_obs_artifact
                    ON algorithm2_observations(artifact_id);
                """
            )

    def _save_run(
        self,
        run_id: str,
        observations: list[EntityObservation],
        observation_artifact: Mapping[str, str],
        result: CorrelationResult,
        threshold: float,
        half_life_days: float,
        attribute_weights: Mapping[str, float] | None,
    ) -> None:
        artifact_ids = set(observation_artifact.values())
        weights_json = json.dumps(attribute_weights or {}, sort_keys=True, default=str)
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO algorithm2_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, datetime.now(timezone.utc).isoformat(), threshold,
                     half_life_days, weights_json, len(artifact_ids), len(observations)),
                )
                for observation in observations:
                    p = observation.provenance
                    connection.execute(
                        """INSERT INTO algorithm2_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (run_id, observation.observation_id,
                         observation_artifact[observation.observation_id],
                         observation.entity_type,
                         json.dumps(observation.attributes, sort_keys=True, default=str),
                         str(observation.event_time) if observation.event_time is not None else None,
                         str(observation.event_time_end) if observation.event_time_end is not None else None,
                         float(observation.extraction_confidence)),
                    )
                for row in result.pair_evidence:
                    connection.execute(
                        "INSERT INTO algorithm2_pair_evidence VALUES (?, ?, ?, ?, ?, ?)",
                        (run_id, row["observation_a"], row["observation_b"], row["score"],
                         row["decision"], json.dumps(row, sort_keys=True, default=str)),
                    )
                for cluster_index, cluster in enumerate(result.clusters, start=1):
                    cluster_id = f"{run_id}:{cluster_index}"
                    for observation_id in cluster:
                        connection.execute(
                            "INSERT INTO algorithm2_cluster_members VALUES (?, ?, ?, ?)",
                            (run_id, cluster_id, observation_id,
                             observation_artifact[observation_id]),
                        )
                connection.executemany(
                    "INSERT INTO algorithm2_errors VALUES (?, ?, ?, ?)",
                    [(run_id, index, error.get("observation_id"), error["error"])
                     for index, error in enumerate(result.errors, start=1)],
                )

    def recent_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM algorithm2_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
