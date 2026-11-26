"""Command-line runner for Algorithm 1."""

import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.hdfr.collection.algorithm1 import HistoricalArtifactCollector


def configure_logging():
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "algorithm1.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )


def main():
    configure_logging()
    print("=" * 60)
    print("ALGORITHM 1: HISTORICAL ARTIFACT DISCOVERY AND COLLECTION")
    print("=" * 60)

    target_url = input("Enter the URL to investigate: ").strip()
    if not target_url:
        print("Error: URL cannot be empty.")
        return

    print("\\nSelect archival sources:")
    print("1. Wayback Machine")
    print("2. Common Crawl")
    print("3. Both sources")
    choice = input("Your choice (1/2/3): ").strip()
    source_options = {
        "1": ["wayback"],
        "2": ["common_crawl"],
        "3": ["wayback", "common_crawl"],
    }
    if choice not in source_options:
        print("Invalid choice.")
        return

    from_date = input("Start date (YYYYMMDD, optional): ").strip() or None
    to_date = input("End date (YYYYMMDD, optional): ").strip() or None

    collector = HistoricalArtifactCollector(
        storage_dir=str(PROJECT_ROOT / "data" / "artifacts"),
        database_path=str(PROJECT_ROOT / "data" / "hdfr.db"),
    )

    try:
        result = collector.run(
            target_url=target_url,
            sources=source_options[choice],
            from_date=from_date,
            to_date=to_date,
        )
        output_path = PROJECT_ROOT / "data" / "algorithm1_result.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=4, ensure_ascii=False),
            encoding="utf-8",
        )

        print("\\n" + "=" * 60)
        print("COLLECTION COMPLETED")
        print("=" * 60)
        print(f"Discovered artifacts: {result['discovered_count']}")
        print(f"Collected artifacts:  {result['collected_count']}")
        print(f"Failed operations:    {result['failed_count']}")
        print(f"Discovery errors:     {result['discovery_error_count']}")
        print(f"Retrieval errors:     {result['retrieval_error_count']}")
        print(f"Storage errors:       {result['storage_error_count']}")
        print(f"Result: {output_path}")

        if result["collected_count"] == 0:
            print("\\nNo artifact was collected. Check the Errors section in:")
            print(output_path)

    except (ValueError, OSError) as exc:
        logging.exception("Algorithm 1 could not complete: %s", exc)
        print(f"\\nAlgorithm 1 failed: {exc}")
    finally:
        collector.session.close()


if __name__ == "__main__":
    main()
