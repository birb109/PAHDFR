import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from src.hdfr.collection.algorithm.algorithm1 import (
    HistoricalArtifactCollector,
    calculate_sha256,
    validate_date,
    validate_url,
)


class Algorithm1Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.collector = HistoricalArtifactCollector(
            storage_dir=str(root / "artifacts"),
            database_path=str(root / "test.db"),
            retry_wait=0,
            max_retries=1,
        )

    def tearDown(self):
        self.collector.session.close()
        self.temp_dir.cleanup()

    def test_validate_url_accepts_http_and_https(self):
        self.assertEqual(validate_url(" https://example.com "), "https://example.com")
        self.assertEqual(validate_url("http://example.com"), "http://example.com")

    def test_validate_url_rejects_invalid_url(self):
        with self.assertRaises(ValueError):
            validate_url("example.com")

    def test_validate_date_rejects_invalid_calendar_date(self):
        with self.assertRaises(ValueError):
            validate_date("20260230", "from_date")

    def test_sha256_is_deterministic(self):
        self.assertEqual(
            calculate_sha256(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_wayback_discovery_parses_cdx_rows(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = [
            ["timestamp", "original", "statuscode", "mimetype", "digest"],
            ["20200101000000", "https://example.com/", "200", "text/html", "abc"],
        ]
        with patch.object(self.collector, "_get_with_retry", return_value=response):
            artifacts = self.collector.discover_wayback("https://example.com/")
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["capture_time"], "20200101000000")
        self.assertIn("20200101000000id_", artifacts[0]["archive_uri"])

    def test_common_crawl_tries_next_index_when_first_is_empty(self):
        first = Mock()
        first.text = ""
        second = Mock()
        second.text = (
            '{"timestamp":"20250101000000",'
            '"url":"https://example.com/",'
            '"filename":"crawl-data/test.warc.gz",'
            '"offset":"100", "length":"200", "mime":"text/html"}'
        )
        with patch.object(
            self.collector, "_get_common_crawl_indexes",
            return_value=["CC-TEST-1", "CC-TEST-2"],
        ), patch.object(
            self.collector, "_get_with_retry",
            side_effect=[first, second],
        ):
            artifacts = self.collector.discover_common_crawl(
                "https://example.com/", "CC-TEST-1"
            )
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["index_name"], "CC-TEST-2")
        self.assertEqual(artifacts[0]["offset"], 100)
        self.assertEqual(artifacts[0]["length"], 200)

    def test_store_artifact_writes_content_metadata_and_database(self):
        artifact = {
            "source": "wayback",
            "source_uri": "https://example.com/",
            "archive_uri": "https://web.archive.org/web/20200101000000id_/https://example.com/",
            "capture_time": "20200101000000",
            "mime_type": "text/html",
        }
        metadata = self.collector.store_artifact(artifact, b"<html>ok</html>")
        self.assertIsNotNone(metadata)
        self.assertTrue(Path(metadata["storage_path"]).exists())
        self.assertEqual(metadata["event_time"], None)
        self.assertEqual(metadata["entity_id"], None)
        saved = self.collector.storage.get_artifact(metadata["artifact_id"])
        self.assertEqual(saved["content_hash"], metadata["content_hash"])

    def test_run_collects_and_persists_discovered_artifact(self):
        artifact = {
            "source": "wayback",
            "source_uri": "https://example.com/",
            "archive_uri": "https://web.archive.org/web/20200101000000id_/https://example.com/",
            "capture_time": "20200101000000",
            "mime_type": "text/html",
        }
        with patch.object(
            self.collector, "discover_wayback", return_value=[artifact]
        ), patch.object(
            self.collector, "retrieve_wayback", return_value=b"<html>history</html>"
        ):
            result = self.collector.run(
                "https://example.com/", sources=["wayback"]
            )
        self.assertEqual(result["discovered_count"], 1)
        self.assertEqual(result["collected_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(len(self.collector.storage.get_artifacts()), 1)

    def test_run_counts_discovery_errors_as_failed_operations(self):
        with patch.object(
            self.collector, "discover_common_crawl",
            side_effect=lambda *args: self.collector.record_error(
                "common_crawl", "discovery", "test timeout", args[0]
            ) or [],
        ):
            result = self.collector.run(
                "https://example.com", sources=["common_crawl"]
            )
        self.assertEqual(result["discovered_count"], 0)
        self.assertEqual(result["collected_count"], 0)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(result["discovery_error_count"], 1)


if __name__ == "__main__":
    unittest.main()
