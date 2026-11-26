"""SQLite persistence for Algorithm 1 artifacts, errors, and run summaries."""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ArtifactStorage:
    """Manage collected artifacts and collection logs."""

    ARTIFACT_EXTRA_COLUMNS = {
        "archive_digest": "TEXT",
        "index_name": "TEXT",
        "warc_filename": "TEXT",
        "warc_offset": "INTEGER",
        "warc_length": "INTEGER",
        "warc_record_id": "TEXT",
    }

    def __init__(self, database_path: str = "data/hdfr.db"):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize_database()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize_database(self):
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    source TEXT,
                    source_uri TEXT,
                    archive_uri TEXT,
                    capture_time TEXT,
                    collection_time TEXT,
                    publication_time TEXT,
                    event_time TEXT,
                    content_hash TEXT,
                    storage_path TEXT,
                    mime_type TEXT,
                    entity_id TEXT,
                    evidence_confidence REAL,
                    supporting_artifacts TEXT,
                    collection_status TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS collection_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT,
                    stage TEXT,
                    url TEXT,
                    message TEXT,
                    timestamp TEXT
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS collection_runs (
                    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_url TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    discovered_count INTEGER,
                    collected_count INTEGER,
                    failed_count INTEGER
                )
            """)

            # Migrate existing databases without deleting collected records.
            existing = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(artifacts)")
            }
            for column, sql_type in self.ARTIFACT_EXTRA_COLUMNS.items():
                if column not in existing:
                    connection.execute(
                        f"ALTER TABLE artifacts ADD COLUMN {column} {sql_type}"
                    )

    def save_artifact(self, metadata: dict[str, Any]):
        fields = [
            "artifact_id", "source", "source_uri", "archive_uri",
            "capture_time", "collection_time", "publication_time",
            "event_time", "content_hash", "storage_path", "mime_type",
            "entity_id", "evidence_confidence", "supporting_artifacts",
            "collection_status", *self.ARTIFACT_EXTRA_COLUMNS.keys(),
        ]
        values = []
        for field in fields:
            value = metadata.get(field)
            if field == "supporting_artifacts":
                value = json.dumps(value or [], ensure_ascii=False)
            values.append(value)

        placeholders = ", ".join("?" for _ in fields)
        columns = ", ".join(fields)
        with self.connect() as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO artifacts ({columns}) "
                f"VALUES ({placeholders})",
                values,
            )

    def save_error(self, error: dict[str, Any]):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO collection_errors
                    (source, stage, url, message, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """, (
                error.get("source"), error.get("stage"), error.get("url"),
                error.get("message"), error.get("timestamp"),
            ))

    def save_run(self, result: dict[str, Any]):
        with self.connect() as connection:
            cursor = connection.execute("""
                INSERT INTO collection_runs (
                    target_url, started_at, finished_at,
                    discovered_count, collected_count, failed_count
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                result.get("target_url"), result.get("started_at"),
                result.get("finished_at"), result.get("discovered_count", 0),
                result.get("collected_count", 0), result.get("failed_count", 0),
            ))
            return cursor.lastrowid

    def get_artifacts(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts ORDER BY collection_time DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_artifact(self, artifact_id: str):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_errors(self):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM collection_errors ORDER BY timestamp DESC"
            ).fetchall()
            return [dict(row) for row in rows]
