import csv
import hashlib
from pickle import TRUE
import random
import re
from datetime import datetime, timedelta
from pathlib import Path
from html import escape


# =========================================================
# 1. PROJECT PATHS
# =========================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATASET_DIR = PROJECT_ROOT / "data" / "dataset_a"

RAW_DIR = DATASET_DIR / "raw"
METADATA_DIR = DATASET_DIR / "metadata"
GROUND_TRUTH_DIR = DATASET_DIR / "ground_truth"

ENTITIES_FILE = GROUND_TRUTH_DIR / "entities.csv"
SNAPSHOTS_FILE = METADATA_DIR / "snapshots.csv"

ENTITY_LABELS_FILE = GROUND_TRUTH_DIR / "entity_labels.csv"
CHANGE_LABELS_FILE = GROUND_TRUTH_DIR / "change_labels.csv"
ENTITY_MATCH_FILE = GROUND_TRUTH_DIR / "entity_match_labels.csv"
EVENTS_FILE = GROUND_TRUTH_DIR / "events.csv"
DUPLICATE_LABELS_FILE = GROUND_TRUTH_DIR / "duplicate_labels.csv"
METADATA_QUALITY_FILE = GROUND_TRUTH_DIR / "metadata_quality_labels.csv"


# =========================================================
# 2. DATASET CONFIGURATION
# =========================================================

SNAPSHOTS_PER_ENTITY = 20
TOTAL_ENTITIES = 12

START_DATE = datetime(2020, 1, 1)

RANDOM_SEED = 42
random.seed(RANDOM_SEED)


# =========================================================
# 3. CREATE REQUIRED DIRECTORIES
# =========================================================

RAW_DIR.mkdir(parents=True, exist_ok=True)
METADATA_DIR.mkdir(parents=True, exist_ok=True)
GROUND_TRUTH_DIR.mkdir(parents=True, exist_ok=True)


# =========================================================
# 4. HELPER FUNCTIONS
# =========================================================

def read_csv(file_path):
    with open(file_path, "r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(file_path, fieldnames, rows):
    with open(file_path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def calculate_sha256(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def slugify(value):
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def format_date(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


print("Dataset A generator initialized.")
print(f"Project root: {PROJECT_ROOT}")
print(f"Dataset directory: {DATASET_DIR}")

# =========================================================
# 5. HISTORICAL WEBSITE CONTENT GENERATION
# =========================================================

def create_website_html(
    organization_name,
    description,
    domain,
    snapshot_number,
    event_type=None,
    content_deletion_active=False
):
    """
    Generate a simulated historical website snapshot.
    """

    projects = [
        "Community Development Program",
        "Research and Innovation Initiative",
        "Public Awareness Campaign",
        "Sustainable Development Project",
        "Digital Transformation Program"
    ]

    project_index = sum(ord(char) for char in description) % len(projects)
    project_name = projects[project_index]

    introduction = description

    if snapshot_number >= 5:
        introduction += (
            " Our organization continues to expand "
            "its activities and improve community services."
        )

    if snapshot_number >= 9:
        introduction += (
            " We are also developing new partnerships "
            "and collaborative initiatives."
        )

    if snapshot_number >= 13:
        introduction += (
            " Our latest activities focus on innovation "
            "and long-term sustainable development."
        )

    if snapshot_number >= 17:
        introduction += (
            " We welcome new partners and community members "
            "to participate in our programs."
        )

    # Remove previously existing content after the deletion event.
    deleted_content = (
        " Our latest activities focus on innovation "
        "and long-term sustainable development."
    )

    if content_deletion_active:
        introduction = introduction.replace(deleted_content, "")

    is_formatting_snapshot = (
        snapshot_number % 4 == 0
        and event_type != "domain_change"
    )

    if is_formatting_snapshot:
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>{escape(organization_name)}</title>
            <meta charset="UTF-8">
            <link rel="canonical" href="https://{escape(domain)}/">
            <style>
                body {{ font-family: Arial; margin: 40px; }}
                h1 {{ color: #245A80; }}
            </style>
        </head>
        <body>
            <header>
                <h1>{escape(organization_name)}</h1>
            </header>
            <main>
                <p>{escape(introduction)}</p>
                <h2>Featured Program</h2>
                <p>{escape(project_name)}</p>
            </main>
            <footer>
                <p>Website: {escape(domain)}</p>
            </footer>
        </body>
        </html>
        """
    else:
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{escape(organization_name)}</title>
            <link rel="canonical" href="https://{escape(domain)}/">
        </head>
        <body>
            <h1>{escape(organization_name)}</h1>
            <p>{escape(introduction)}</p>
            <h2>Featured Program</h2>
            <p>{escape(project_name)}</p>
            <p>Website: {escape(domain)}</p>
        </body>
        </html>
        """

    return html
# =========================================================
# 6. HISTORICAL CHANGE SCENARIOS
# =========================================================

def get_entity_scenarios(entity_id, canonical_name):
    """
    Define controlled historical changes for each entity.
    """

    scenarios = {
        "rename": None,
        "domain_change": None,
        "content_deletion": None,
        "missing_timestamp": [7],
        "duplicate_snapshot": [10],
        "ambiguous_match": False
    }

    # Rename selected entities
    if entity_id in ["E001", "E003", "E005", "E008"]:
        scenarios["rename"] = {
            "snapshot": 10,
            "old_name": canonical_name,
            "new_name": canonical_name + " International"
        }

    # Domain changes for selected entities
    if entity_id in ["E002", "E004", "E006", "E009"]:
        scenarios["domain_change"] = {
            "snapshot": 12,
            "old_domain": slugify(canonical_name) + ".org",
            "new_domain": slugify(canonical_name) + "-global.org"
        }
    # Content deletion for a selected entity
    if entity_id == "E009":
        scenarios["content_deletion"] = {
            "snapshot": 15,
            "deleted_content": (
                "Our latest activities focus on innovation "
                "and long-term sustainable development."
            )
        }

    # Selected entities have ambiguous matching cases
    if entity_id in ["E003", "E007", "E010", "E012"]:
        scenarios["ambiguous_match"] = True

    return scenarios


def get_snapshot_identity(entity, snapshot_number):
    """
    Return the historical name and domain of a snapshot.
    """

    entity_id = entity["entity_id"]
    canonical_name = entity["canonical_name"]

    scenarios = get_entity_scenarios(
        entity_id,
        canonical_name
    )

    historical_name = canonical_name
    domain = slugify(canonical_name) + ".org"

    rename = scenarios["rename"]

    if rename and snapshot_number >= rename["snapshot"]:
        historical_name = rename["new_name"]

    domain_change = scenarios["domain_change"]

    if domain_change and snapshot_number >= domain_change["snapshot"]:
        domain = domain_change["new_domain"]

    return historical_name, domain, scenarios


def get_scenario_description(scenarios, snapshot_number):
    """
    Identify the controlled event occurring at a snapshot.
    """

    rename = scenarios["rename"]

    if rename and snapshot_number == rename["snapshot"]:
        return "rename"

    domain_change = scenarios["domain_change"]

    if domain_change and snapshot_number == domain_change["snapshot"]:
        return "domain_change"

    content_deletion = scenarios["content_deletion"]

    if (
        content_deletion
        and snapshot_number == content_deletion["snapshot"]
    ):
        return "content_deletion"

    if snapshot_number % 4 == 0:
        return "formatting_change"

    if snapshot_number in [5, 9, 13, 17]:
        return "content_change"

    return "no_change"
# =========================================================
# 7. GENERATE SNAPSHOTS AND GROUND TRUTH
# =========================================================

def generate_dataset():
    entities = read_csv(ENTITIES_FILE)

    if len(entities) != TOTAL_ENTITIES:
        raise ValueError(
            f"Expected {TOTAL_ENTITIES} entities, "
            f"but found {len(entities)}."
        )

    snapshot_rows = []
    entity_label_rows = []
    change_label_rows = []
    event_rows = []
    duplicate_rows = []
    metadata_quality_rows = []
    artifact_lookup = {}

    for entity in entities:
        entity_id = entity["entity_id"]
        canonical_name = entity["canonical_name"]
        description = entity["description"]

        for number in range(1, SNAPSHOTS_PER_ENTITY + 1):
            artifact_id = f"{entity_id}_S{number:02d}"

            historical_name, domain, scenarios = (
                get_snapshot_identity(entity, number)
            )

            scenario = get_scenario_description(
                scenarios, number
            )

            # Create a controlled exact duplicate for E011 and E012.
            is_duplicate = (
                entity_id in ["E011", "E012"]
                and number == 10
            )

            if is_duplicate:
                html = artifact_lookup[
                    f"{entity_id}_S09"
                ]["html"]
            else:
                content_deletion = scenarios["content_deletion"]

                content_deletion_active = (
                    content_deletion is not None
                    and number >= content_deletion["snapshot"]
             )

                html = create_website_html(
                    historical_name,
                    description,
                    domain,
                    number,
                    scenario,
                    content_deletion_active
                )

            # Save each snapshot as a separate HTML artifact.
            relative_path = (
                Path("raw")
                / entity_id
                / f"{artifact_id}.html"
            )

            output_path = DATASET_DIR / relative_path
            output_path.parent.mkdir(
                parents=True, exist_ok=True
            )
            output_path.write_text(
                html, encoding="utf-8"
            )

            content_hash = hashlib.sha256(
                output_path.read_bytes()
            ).hexdigest()

            # Use a deterministic simulated capture date.
            capture_date = START_DATE + timedelta(
                days=(number - 1) * 90
            )

            # Snapshot 7 intentionally has no capture timestamp.
            capture_time = (
                ""
                if number == 7
                else format_date(capture_date)
            )

            # Record missing timestamp as metadata quality ground truth.
            if number == 7:
                metadata_quality_rows.append({
                    "quality_label_id": f"MQ_{entity_id}",
                    "artifact_id": artifact_id,
                    "metadata_field": "capture_time",
                    "quality_issue": "missing_timestamp",
                    "ground_truth": "TRUE",
                    "notes": "Capture timestamp is intentionally missing."
                })

            publication_time = format_date(
                capture_date - timedelta(days=7)
            )

            event_time = ""

            # Record organization renaming events.
            rename = scenarios["rename"]

            if rename and number == rename["snapshot"]:
                event_time = format_date(capture_date)

                event_rows.append({
                    "event_id": f"EV_{entity_id}_RENAME",
                    "entity_id": entity_id,
                    "event_type": "rename",
                    "event_date": format_date(capture_date),
                    "previous_value": rename["old_name"],
                    "new_value": rename["new_name"],
                    "description": "Organization name changed"
                })

            # Record domain change events.
            domain_change = scenarios["domain_change"]

            if (
                domain_change
                and number == domain_change["snapshot"]
            ):
                event_time = format_date(capture_date)

                event_rows.append({
                    "event_id": f"EV_{entity_id}_DOMAIN",
                    "entity_id": entity_id,
                    "event_type": "domain_change",
                    "event_date": format_date(capture_date),
                    "previous_value": domain_change["old_domain"],
                    "new_value": domain_change["new_domain"],
                    "description": "Website domain changed"
                })

            source_uri = f"https://{domain}/"
            archive_uri = (
                f"https://synthetic-archive.example/"
                f"{artifact_id}"
            )

            snapshot_rows.append({
                "artifact_id": artifact_id,
                "source_uri": source_uri,
                "archive_uri": archive_uri,
                "source_name": "Synthetic Controlled Benchmark",
                "capture_time": capture_time,
                "publication_time": publication_time,
                "event_time": event_time,
                "html_path": str(relative_path).replace("\\", "/"),
                "content_hash": content_hash
            })

            entity_label_rows.append({
                "artifact_id": artifact_id,
                "true_entity_id": entity_id,
                "annotation_status": "verified",
                "notes": "Synthetic ground-truth assignment"
            })

            artifact_lookup[artifact_id] = {
                "html": html,
                "entity_id": entity_id,
                "number": number
            }

            # Label changes between consecutive snapshots.
            if number > 1:
                previous_id = f"{entity_id}_S{number - 1:02d}"

                if is_duplicate:
                    change_type = "duplicate"
                    changed_element = "entire_snapshot"
                elif scenario == "rename":
                    change_type = "organization_rename"
                    changed_element = "organization_name"
                elif scenario == "domain_change":
                    change_type = "domain_change"
                    changed_element = "website_domain"
                elif scenario == "formatting_change":
                    change_type = "formatting_only"
                    changed_element = "HTML_structure_or_style"
                elif scenario == "content_change":
                    change_type = "content_addition"
                    changed_element = "website_content"
                elif scenario == "content_deletion":
                    change_type = "content_deletion"
                    changed_element = "website_content"
                else:
                    change_type = "no_change"
                    changed_element = "none"

                if change_type != "no_change":
                    change_label_rows.append({
                        "change_id": f"CH_{artifact_id}",
                        "previous_artifact_id": previous_id,
                        "current_artifact_id": artifact_id,
                        "change_type": change_type,
                        "changed_element": changed_element,
                        "ground_truth": "TRUE",
                        "description": (
                            f"Controlled {change_type} scenario"
                        )
                    })

            # Record exact duplicate pairs.
            if is_duplicate:
                duplicate_rows.append({
                    "duplicate_id": f"DUP_{artifact_id}",
                    "artifact_id_1": f"{entity_id}_S09",
                    "artifact_id_2": artifact_id,
                    "is_duplicate": "TRUE",
                    "duplicate_type": "exact",
                    "notes": "Identical HTML content"
                })

    # Create ambiguous cross-entity matching examples.
    ambiguous_pairs = [
        ("E003_S15", "E007_S15"),
        ("E007_S15", "E010_S15"),
        ("E010_S15", "E012_S15")
    ]

    entity_match_rows = []

    for index, (artifact_1, artifact_2) in enumerate(
        ambiguous_pairs, start=1
    ):
        entity_match_rows.append({
            "match_id": f"MATCH_{index:03d}",
            "artifact_id_1": artifact_1,
            "artifact_id_2": artifact_2,
            "same_entity": "FALSE",
            "match_type": "ambiguous",
            "notes": "Different entities with potentially similar context"
        })

    # Save generated metadata and ground-truth labels.
    write_csv(
        SNAPSHOTS_FILE,
        [
            "artifact_id", "source_uri", "archive_uri",
            "source_name", "capture_time", "publication_time",
            "event_time", "html_path", "content_hash"
        ],
        snapshot_rows
    )

    write_csv(
        ENTITY_LABELS_FILE,
        [
            "artifact_id", "true_entity_id",
            "annotation_status", "notes"
        ],
        entity_label_rows
    )

    write_csv(
        CHANGE_LABELS_FILE,
        [
            "change_id", "previous_artifact_id",
            "current_artifact_id", "change_type",
            "changed_element", "ground_truth", "description"
        ],
        change_label_rows
    )

    write_csv(
        EVENTS_FILE,
        [
            "event_id", "entity_id", "event_type",
            "event_date", "previous_value",
            "new_value", "description"
        ],
        event_rows
    )

    write_csv(
        DUPLICATE_LABELS_FILE,
        [
            "duplicate_id", "artifact_id_1",
            "artifact_id_2", "is_duplicate",
            "duplicate_type", "notes"
        ],
        duplicate_rows
    )

    write_csv(
        ENTITY_MATCH_FILE,
        [
            "match_id", "artifact_id_1", "artifact_id_2",
            "same_entity", "match_type", "notes"
        ],
        entity_match_rows
    )

    write_csv(
        METADATA_QUALITY_FILE,
        [
            "quality_label_id", "artifact_id",
            "metadata_field", "quality_issue",
            "ground_truth", "notes"
        ],
        metadata_quality_rows
    )

    print("\nDataset A generation completed.")
    print(f"Entities: {len(entities)}")
    print(f"Snapshots: {len(snapshot_rows)}")
    print(f"Change labels: {len(change_label_rows)}")
    print(f"Events: {len(event_rows)}")
    print(f"Duplicate pairs: {len(duplicate_rows)}")
    print(f"Ambiguous match pairs: {len(entity_match_rows)}")
    print(f"Metadata quality labels: {len(metadata_quality_rows)}")
# =========================================================
# 8. MAIN
# =========================================================

if __name__ == "__main__":
    generate_dataset()