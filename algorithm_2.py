from __future__ import annotations

"""Algorithm 2: Temporal-Aware Entity Correlation for HDFR.

Correlates entity observations extracted from archived web artifacts. It uses
identity evidence, temporal evidence, and provenance while keeping archive
capture time separate from an inferred historical event time.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import unicodedata
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class Provenance:
    """Traceable origin of an observation in the archived evidence corpus."""

    archive_service: str
    original_url: str
    capture_uri: str
    capture_timestamp: datetime | str | None
    artifact_id: str
    content_hash: str | None = None


@dataclass(frozen=True)
class EntityObservation:
    """An entity mention extracted from one preserved web artifact."""

    observation_id: str
    entity_type: str
    attributes: Mapping[str, Any]
    provenance: Provenance
    # Optional interval/date inferred from the page's content; this is not the
    # same as the archive's capture timestamp.
    event_time: datetime | str | None = None
    event_time_end: datetime | str | None = None
    extraction_confidence: float = 1.0


@dataclass
class CorrelationResult:
    clusters: list[list[str]]
    pair_evidence: list[dict[str, Any]]
    errors: list[dict[str, str]]


def _parse_time(value: datetime | str | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
    else:
        raise TypeError("time must be a datetime, ISO-8601 string, or None")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    text = re.sub(r"\s+", " ", text)
    return text or None


def _similarity(left: Any, right: Any) -> float | None:
    a, b = _normalize(left), _normalize(right)
    if a is None or b is None:
        return None
    if a == b:
        return 1.0
    tokens_a = set(re.findall(r"[\w]+", a))
    tokens_b = set(re.findall(r"[\w]+", b))
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _temporal_evidence(a: EntityObservation, b: EntityObservation,
                       capture_a: datetime | None, capture_b: datetime | None,
                       event_a: datetime | None, event_a_end: datetime | None,
                       event_b: datetime | None, event_b_end: datetime | None,
                       half_life_days: float) -> tuple[float, str]:
    """Score temporal compatibility using event dates when present.

    Capture times establish when an archive saw a page; they are used as a
    lower-confidence fallback, never silently equated with the event date.
    """
    if event_a is not None and event_b is not None:
        end_a = event_a_end or event_a
        end_b = event_b_end or event_b
        if end_a < event_b or end_b < event_a:
            return 0.35, "inferred_event_intervals_do_not_overlap"
        return 1.0, "inferred_event_intervals_overlap"
    if capture_a is not None and capture_b is not None:
        days = abs((capture_a - capture_b).total_seconds()) / 86400
        factor = max(0.1, 0.5 ** (days / half_life_days))
        return factor, f"archive_capture_times_{days:.1f}_days_apart_event_date_unknown"
    return 0.7, "insufficient_temporal_metadata_neutral_discount"


def correlate_archived_entities(
    observations: Iterable[EntityObservation],
    *,
    threshold: float = 0.72,
    half_life_days: float = 365.0,
    attribute_weights: Mapping[str, float] | None = None,
    minimum_provenance_fields: tuple[str, ...] = (
        "archive_service", "original_url", "capture_uri", "artifact_id"),
) -> CorrelationResult:
    """Correlate entity observations and return auditable evidence.

    Pair score = weighted identity similarity × temporal compatibility ×
    extraction confidence. Invalid observations are reported and skipped.
    All parameters and evidence used in a decision are included in the result.
    """
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be finite and between 0 and 1")
    if not math.isfinite(half_life_days) or half_life_days <= 0:
        raise ValueError("half_life_days must be finite and greater than zero")

    errors: list[dict[str, str]] = []
    valid: list[tuple[EntityObservation, datetime | None, datetime | None,
                      datetime | None, datetime | None]] = []
    seen: set[str] = set()

    for index, item in enumerate(observations):
        try:
            if not isinstance(item, EntityObservation):
                raise TypeError("expected EntityObservation")
            oid = str(item.observation_id).strip()
            if not oid:
                raise ValueError("observation_id is required")
            if oid in seen:
                errors.append({"observation_id": oid, "error": "duplicate ID; skipped"})
                continue
            if not str(item.entity_type).strip() or not isinstance(item.attributes, Mapping):
                raise ValueError("entity_type and mapping attributes are required")
            if not isinstance(item.provenance, Provenance):
                raise ValueError("provenance is required")
            missing = [name for name in minimum_provenance_fields
                       if not str(getattr(item.provenance, name, "")).strip()]
            if missing:
                raise ValueError("missing provenance fields: " + ", ".join(missing))
            confidence = float(item.extraction_confidence)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                raise ValueError("extraction_confidence must be between 0 and 1")

            capture = _parse_time(item.provenance.capture_timestamp)
            event = _parse_time(item.event_time)
            event_end = _parse_time(item.event_time_end)
            if event is not None and event_end is not None and event_end < event:
                raise ValueError("event_time_end precedes event_time")
            seen.add(oid)
            valid.append((item, capture, event, event_end, None))
        except (TypeError, ValueError, OverflowError) as exc:
            errors.append({"observation_id": str(getattr(item, "observation_id", index)),
                           "error": str(exc)})

    weights = dict(attribute_weights or {})
    pair_evidence: list[dict[str, Any]] = []
    accepted: list[tuple[str, str]] = []

    for i, (a, capture_a, event_a, event_a_end, _) in enumerate(valid):
        for b, capture_b, event_b, event_b_end, _ in valid[i + 1:]:
            if _normalize(a.entity_type) != _normalize(b.entity_type):
                continue
            evidence: list[tuple[str, float, float]] = []
            invalid_weight = False
            for key in sorted(set(a.attributes) & set(b.attributes)):
                try:
                    weight = float(weights.get(key, 1.0))
                except (TypeError, ValueError):
                    weight = math.nan
                if not math.isfinite(weight) or weight < 0:
                    errors.append({"observation_id": f"{a.observation_id},{b.observation_id}",
                                   "error": f"invalid weight for {key!r}; pair skipped"})
                    invalid_weight = True
                    break
                similarity = _similarity(a.attributes[key], b.attributes[key])
                if similarity is not None and weight > 0:
                    evidence.append((key, similarity, weight))
            if invalid_weight or not evidence:
                continue

            identity_score = sum(score * weight for _, score, weight in evidence) / sum(
                weight for _, _, weight in evidence)
            temporal_score, temporal_reason = _temporal_evidence(
                a, b, capture_a, capture_b, event_a, event_a_end,
                event_b, event_b_end, half_life_days)
            confidence = min(float(a.extraction_confidence), float(b.extraction_confidence))
            final_score = identity_score * temporal_score * confidence
            decision = "correlate" if final_score >= threshold else "review"
            row = {
                "observation_a": a.observation_id,
                "observation_b": b.observation_id,
                "artifact_a": a.provenance.artifact_id,
                "artifact_b": b.provenance.artifact_id,
                "archive_a": a.provenance.archive_service,
                "archive_b": b.provenance.archive_service,
                "capture_a": a.provenance.capture_timestamp,
                "capture_b": b.provenance.capture_timestamp,
                "event_time_a": a.event_time,
                "event_time_b": b.event_time,
                "identity_score": round(identity_score, 4),
                "temporal_score": round(temporal_score, 4),
                "extraction_confidence": round(confidence, 4),
                "score": round(final_score, 4),
                "temporal_reason": temporal_reason,
                "attribute_evidence": {key: round(score, 4)
                                       for key, score, _ in evidence},
                "decision": decision,
            }
            pair_evidence.append(row)
            if decision == "correlate":
                accepted.append((a.observation_id, b.observation_id))

    # Connected components yield candidate entity histories. Pair evidence is
    # retained because transitive membership alone is not sufficient proof.
    parent = {item.observation_id: item.observation_id for item, *_ in valid}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    for left, right in accepted:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left
    groups: dict[str, list[str]] = {}
    for observation_id in parent:
        groups.setdefault(find(observation_id), []).append(observation_id)
    clusters = sorted((sorted(ids) for ids in groups.values()), key=lambda ids: ids[0])
    return CorrelationResult(clusters, pair_evidence, errors)


if __name__ == "__main__":
    p1 = Provenance("Archive-A", "https://example.org/about",
                    "https://archive-a.example/capture/1", "2024-01-10T10:00:00Z", "artifact-1")
    p2 = Provenance("Archive-B", "https://example.org/about",
                    "https://archive-b.example/capture/2", "2024-02-01T10:00:00Z", "artifact-2")
    data = [
        EntityObservation("obs-1", "organization",
                          {"name": "Example Research Group", "domain": "example.org"}, p1),
        EntityObservation("obs-2", "organization",
                          {"name": "Example Research Group", "domain": "example.org"}, p2),
    ]
    print(correlate_archived_entities(data))


