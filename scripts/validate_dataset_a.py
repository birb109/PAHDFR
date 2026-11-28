import csv
import hashlib
from importlib import metadata
from importlib.metadata import metadata
from pathlib import Path


# =========================================================
# 1. PATHS
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = PROJECT_ROOT / "data" / "dataset_a"

RAW_DIR = DATASET_DIR / "raw"
METADATA_FILE = DATASET_DIR / "metadata" / "snapshots.csv"
ENTITY_LABELS_FILE = DATASET_DIR / "ground_truth" / "entity_labels.csv"
CHANGE_LABELS_FILE = DATASET_DIR / "ground_truth" / "change_labels.csv"
DUPLICATE_FILE = DATASET_DIR / "ground_truth" / "duplicate_labels.csv"
METADATA_QUALITY_FILE = (
    DATASET_DIR / "ground_truth" / "metadata_quality_labels.csv"
)
EVENTS_FILE = DATASET_DIR / "ground_truth" / "events.csv"
ENTITY_MATCH_FILE = (
    DATASET_DIR / "ground_truth" / "entity_match_labels.csv"
)


# =========================================================
# 2. HELPER FUNCTIONS
# =========================================================

def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def calculate_sha256(file_path):
    sha256 = hashlib.sha256()

    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(4096), b""):
            sha256.update(chunk)

    return sha256.hexdigest()


# =========================================================
# 3. VALIDATE DATASET
# =========================================================

def validate_dataset():
    metadata = read_csv(METADATA_FILE)
    entity_labels = read_csv(ENTITY_LABELS_FILE)
    change_labels = read_csv(CHANGE_LABELS_FILE)
    duplicate_labels = read_csv(DUPLICATE_FILE)
    metadata_quality_labels = read_csv(
    METADATA_QUALITY_FILE
    )
    events = read_csv(EVENTS_FILE)
    entity_match_labels = read_csv(ENTITY_MATCH_FILE)

    errors = []
    checked_hashes = 0

    print("=" * 60)
    print("DATASET A - INTEGRITY VALIDATION")
    print("=" * 60)

    # Check snapshot count
    if len(metadata) != 240:
        errors.append(
            f"Expected 240 metadata records, found {len(metadata)}"
        )

    # Check unique artifact IDs
    artifact_ids = [
        row["artifact_id"] for row in metadata
    ]

    if len(artifact_ids) != len(set(artifact_ids)):
        errors.append("Duplicate artifact IDs found in metadata")

    # Check HTML paths and SHA-256
    for row in metadata:
        artifact_id = row["artifact_id"]

        html_path = (
            DATASET_DIR / row["html_path"]
        ).resolve()

        if not html_path.is_relative_to(RAW_DIR.resolve()):
            errors.append(
                f"{artifact_id}: HTML path is outside raw directory"
            )
            continue

        if not html_path.exists():
            errors.append(
                f"{artifact_id}: HTML file does not exist"
            )
            continue

        actual_hash = calculate_sha256(html_path)

        if actual_hash != row["content_hash"]:
            errors.append(
                f"{artifact_id}: SHA-256 mismatch"
            )
        else:
            checked_hashes += 1

    # Check entity labels
    if len(entity_labels) != 240:
        errors.append(
            f"Expected 240 entity labels, found {len(entity_labels)}"
        )

    label_ids = {
        row["artifact_id"] for row in entity_labels
    }

    if set(artifact_ids) != label_ids:
        errors.append(
            "Metadata and entity label artifact IDs do not match"
        )

    # Check change label references
    known_ids = set(artifact_ids)

    for row in change_labels:
        if row["previous_artifact_id"] not in known_ids:
            errors.append(
                f"Unknown previous artifact: "
                f"{row['previous_artifact_id']}"
            )

        if row["current_artifact_id"] not in known_ids:
            errors.append(
                f"Unknown current artifact: "
                f"{row['current_artifact_id']}"
            )

    # Check duplicate label references
    for row in duplicate_labels:
        if row["artifact_id_1"] not in known_ids:
            errors.append(
                f"Unknown duplicate artifact: {row['artifact_id_1']}"
            )

        if row["artifact_id_2"] not in known_ids:
            errors.append(
                f"Unknown duplicate artifact: {row['artifact_id_2']}"
            )

        # Check metadata quality labels
    if len(metadata_quality_labels) != 12:
        errors.append(
            "Expected 12 metadata quality labels, "
            f"found {len(metadata_quality_labels)}"
        )

    quality_artifact_ids = set()

    for row in metadata_quality_labels:
        artifact_id = row["artifact_id"]

        if artifact_id not in known_ids:
            errors.append(
                f"Unknown metadata quality artifact: {artifact_id}"
            )
            continue

        if artifact_id in quality_artifact_ids:
            errors.append(
                f"Duplicate metadata quality label: {artifact_id}"
            )

        quality_artifact_ids.add(artifact_id)

        if row["metadata_field"] != "capture_time":
            errors.append(
                f"{artifact_id}: Incorrect metadata field"
            )

        if row["quality_issue"] != "missing_timestamp":
            errors.append(
                f"{artifact_id}: Incorrect quality issue"
            )

        if row["ground_truth"].upper() != "TRUE":
            errors.append(
                f"{artifact_id}: Invalid ground truth"
            )

        snapshot = next(
            (
                item for item in metadata
                if item["artifact_id"] == artifact_id
            ),
            None
        )

        if snapshot is not None and snapshot["capture_time"].strip():
            errors.append(
                f"{artifact_id}: capture_time should be empty"
            )

    # Check that all S07 snapshots have missing timestamp labels
    expected_quality_ids = {
        f"E{number:03d}_S07"
        for number in range(1, 13)
    }

    if quality_artifact_ids != expected_quality_ids:
        errors.append(
            "Metadata quality labels do not cover all 12 S07 snapshots"
        )

        # Check event records
    if len(events) != 8:
        errors.append(
            f"Expected 8 events, found {len(events)}"
        )

    known_entity_ids = {
        row["true_entity_id"] for row in entity_labels
    }

    for row in events:
        if row["entity_id"] not in known_entity_ids:
            errors.append(
                f"Unknown event entity: {row['entity_id']}"
            )

        if row["event_type"] not in [
            "rename", "domain_change"
        ]:
            errors.append(
                f"Invalid event type: {row['event_type']}"
            )

    # Check entity match labels
    if len(entity_match_labels) != 3:
        errors.append(
            "Expected 3 entity match labels, "
            f"found {len(entity_match_labels)}"
        )

    for row in entity_match_labels:
        if row["artifact_id_1"] not in known_ids:
            errors.append(
                f"Unknown match artifact: {row['artifact_id_1']}"
            )

        if row["artifact_id_2"] not in known_ids:
            errors.append(
                f"Unknown match artifact: {row['artifact_id_2']}"
            )

        if row["match_type"] != "ambiguous":
            errors.append(
                f"Unexpected match type: {row['match_type']}"
            )

        if row["same_entity"].upper() != "FALSE":
            errors.append(
                f"Unexpected same_entity label: {row['match_id']}"
            )
    # Results
    print(f"Metadata records: {len(metadata)}")
    print(f"Entity labels: {len(entity_labels)}")
    print(f"Change labels: {len(change_labels)}")
    print(f"Duplicate labels: {len(duplicate_labels)}")
    print(
        f"Metadata quality labels: "
        f"{len(metadata_quality_labels)}"
    )
    print(f"Event records: {len(events)}")
    print(f"Entity match labels: {len(entity_match_labels)}")
    print(f"Valid SHA-256 hashes: {checked_hashes}")

    print("-" * 60)

    if errors:
        print(f"VALIDATION FAILED: {len(errors)} issue(s) found.")

        for error in errors:
            print("[ERROR]", error)
    else:
        print("VALIDATION PASSED.")
        print("All checked files and hashes are consistent.")


if __name__ == "__main__":
    validate_dataset()
