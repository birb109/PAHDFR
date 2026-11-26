# Algorithm 2: Temporal-Aware Entity Correlation

## Integration with Algorithm 1

The executable integration is `src/hdfr/collection/algorithm/algorithm2_pipeline.py`. It reads successful artifact records through Algorithm 1's `ArtifactStorage` from the same SQLite file, `data/hdfr.db`, and uses each stored row's artifact ID, source URL, archive URI, capture timestamp, content hash, and storage path as evidence provenance. It does not modify Algorithm 1 or copy its artifacts into a second database.

Algorithm 1 preserves artifacts and provenance; it does not extract entity mentions. Therefore, Algorithm 2 accepts annotations tied to Algorithm 1 artifact IDs. The Streamlit page displays the stored page text for the selected artifact IDs, and the annotations then specify the entities/attributes identified in those pages. Algorithm 2 writes run metadata, observations, pair evidence, clusters, and errors into additional `algorithm2_*` tables in Algorithm 1's existing database.

Annotation input format:

```json
[
  {
    "artifact_id": "<artifact_id from the Algorithm 1 artifacts table>",
    "entities": [
      {
        "entity_type": "organization",
        "attributes": {"name": "Example Research Group", "domain": "example.org"},
        "event_time": null,
        "event_time_end": null,
        "extraction_confidence": 0.9
      }
    ]
  }
]
```

Run the UI from the repository root with `streamlit run src/hdfr/app/app.py`. Run Algorithm 1 first so the shared database contains artifacts, then select those artifacts, annotate the entities shown in their stored content, and run Algorithm 2. A run can be reviewed in the `algorithm2_runs`, `algorithm2_observations`, `algorithm2_pair_evidence`, `algorithm2_cluster_members`, and `algorithm2_errors` tables.

## Role in the HDFR framework

This algorithm supports **Provenance-Aware Historical Digital Footprint Reconstruction: A Temporal OSINT Framework with Evidence Correlation and Reproducible Evaluation**. It addresses the entity-correlation stage of the proposed workflow: observations extracted from preserved web artifacts are compared and linked into candidate entity histories. Every decision retains evidence and archival provenance so that the result can be inspected and reproduced.

The algorithm assumes artifact discovery, preservation, deduplication, and entity extraction have already produced observations. It does not claim that an archive capture date is the date of the event described by the page. Archive capture times and inferred event dates are retained as separate evidence.

## Input

`correlate_archived_entities(observations, threshold=0.72, half_life_days=365.0, attribute_weights=None, minimum_provenance_fields=...)`

Each `EntityObservation` contains:

- `observation_id`: stable identifier for this entity mention/observation.
- `entity_type`: entity class, such as person, organization, or domain.
- `attributes`: extracted identity evidence, for example name, former name, domain, URL, email, or identifier.
- `provenance`: `Provenance` object identifying archive service, original URL, capture URI, capture timestamp, artifact ID, and optionally a content hash.
- `event_time` and `event_time_end`: optional date or interval inferred from the artifact's content, distinct from capture time.
- `extraction_confidence`: confidence assigned by the upstream extraction step, between 0 and 1.

Configuration parameters:

- `threshold`: minimum final score for an accepted pair link, between 0 and 1.
- `half_life_days`: temporal decay half-life used when event dates are unavailable and archive capture times are compared.
- `attribute_weights`: optional importance weights for attributes. Unspecified weights default to 1.
- `minimum_provenance_fields`: provenance fields required for an observation to participate. By default, archive service, original URL, capture URI, and artifact ID are mandatory.

For reproducibility, preserve the archive identifiers, original URL, capture URI and timestamp, artifact identifier/content hash, extraction version, algorithm version, and parameter values in the wider HDFR evidence store. This implementation returns the key pair evidence and assumes the upstream pipeline retains the full artifact and extraction metadata.

## Output

The function returns `CorrelationResult` with:

- `clusters`: candidate groups of observation IDs connected by accepted pair links. Valid unlinked observations appear as singleton clusters.
- `pair_evidence`: per-pair identity and temporal scores, extraction confidence, attribute-level evidence, provenance identifiers, temporal rationale, and decision (`correlate` or `review`).
- `errors`: validation problems tied to an observation or pair. Invalid observations are skipped while processing continues for valid inputs.

Clusters are candidate histories, not verified real-world identities. Review pair-level evidence and contradictions before interpreting a cluster as one entity. Connected-component clustering is transitive; a path of accepted links may join records whose direct pair was never accepted.

## Processing steps

1. **Validate the evidence records.** Require a unique observation ID, entity type, attribute mapping, provenance fields, valid confidence, and parseable timestamps. Reject invalid intervals where the end precedes the start.
2. **Compare compatible entity types.** Generate pairs only for observations with the same normalized entity type.
3. **Measure identity evidence.** Normalize attribute values using Unicode NFKC, case folding, and whitespace normalization. Exact matches score 1.0; other strings use token Jaccard similarity. Missing attributes are omitted. Calculate the weighted mean across comparable attributes.
4. **Measure temporal compatibility.** When both observations have inferred event dates/intervals, overlapping intervals score 1.0 and non-overlapping intervals score 0.35. Otherwise, when both archive capture timestamps exist, apply exponential decay based on their distance, with `half_life_days` and a minimum factor of 0.1. If neither comparison is possible, apply a neutral discount of 0.7. Capture-time comparisons are explicitly labeled as a fallback and are not treated as event dates.
5. **Account for extraction confidence.** Multiply identity agreement by temporal compatibility and the lower extraction confidence of the pair: `final_score = identity_score × temporal_score × min(confidence_a, confidence_b)`.
6. **Decide and preserve the rationale.** Scores at or above `threshold` become candidate links. Lower scores remain in the output for review. Record the input artifact/provenance references, contributing attributes, component scores, and temporal rationale for each evaluated pair.
7. **Construct candidate histories.** Group accepted links into connected components, retaining pair evidence for auditing and reproducible evaluation.

## Error handling

- Empty or duplicate observation IDs, missing required provenance, invalid entity types/attributes, malformed timestamps, invalid confidence, and reversed event intervals are added to `errors`; the affected observation is skipped.
- A duplicate ID is skipped after its first valid occurrence.
- Missing event dates do not stop processing. Capture timestamps may provide a lower-confidence temporal comparison; if these are also unavailable, the neutral temporal factor is used.
- Pairs without any shared, non-missing attributes are omitted because they have insufficient identity evidence.
- Negative, non-numeric, or non-finite attribute weights are reported and cause that pair to be skipped.
- An invalid threshold or temporal half-life raises `ValueError` before pair processing.
- Provenance is never fabricated. An observation missing required provenance is rejected, and the error identifies missing fields.
- A below-threshold pair is returned for review and does not create a cluster link.

## Reference implementation

See [algorithm_2.py](../algorithm_2.py). It uses only the Python standard library. It performs correlation in memory; persistence is handled by the broader HDFR storage layer described in [HDFR_FRAMEWORK.md](../HDFR_FRAMEWORK.md). The scoring values are explicit baseline heuristics; calibrate them against labeled entity pairs and report threshold selection and evaluation metrics in the study.

## Relationship to the abstract's contribution

Algorithm 2 is one component of the integrated HDFR workflow described in the abstract. It consumes preserved, deduplicated artifacts and extracted entities, contributes cross-source temporal correlation, and emits provenance-linked evidence for subsequent timeline reconstruction. It does not perform artifact discovery, content-change detection, or final event-date adjudication; those stages must provide inputs or consume its outputs within the larger reproducible pipeline.


