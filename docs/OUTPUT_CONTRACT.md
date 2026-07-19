# Project 2 Output Contract

Contract version: `project2-result-envelope-v2`.

The stable result path is:

```text
outputs/project2/<method>/<model>/<dataset_id>/<prompt_variant>/seed_<seed>/
```

Each result contains `assembled_prompt.txt`, `raw_response.json`,
`raw_output.txt`, `normalized_output.txt`, `final_ontology.ttl`,
`generation_metadata.json`, `parse_metadata.json`, `error.json`, and
`status.json`. Multi-call methods additionally contain
`steps/step_NN/{request.json,assembled_prompt.txt,raw_response.json,raw_output.txt,normalized_output.txt,final_ontology.ttl,parse_metadata.json,step_metadata.json}`.
When a later step consumes a prior ontology, that step also preserves the exact
propagated text in `previous_output_input.txt`.
`step_metadata.json` includes the assembled-prompt SHA-256, native telemetry,
HTTP attempt count, retry-relevant options, and pipeline-stage annotations.

All files are written through same-directory temporary files and atomic rename;
`status.json` is written last. Before a retry or forced write, an existing
result directory is renamed to `seed_<seed>__archive_<timestamp>`, preserving
its raw response and error evidence.

`parse_metadata.json` independently records raw, normalized, and final parse
success, detected formats, diagnostics, normalization indicators, and repair
metadata. A normalized or repaired success never changes
`raw_parse_success`.

`generation_metadata.json` retains method/provider/model/options, effective
keep_alive, native telemetry per internal call, call/attempt counts, prompt and
resource hashes, repository/dataset identities, cache basis, and cache key.
Future envelopes also store `cache_identity_schema_version: 2` in both
`generation_metadata.json` and `status.json`. The schema marker participates in
the cache identity itself.
The runner also returns the adapter metadata and paths to generation/parse
metadata instead of discarding them.

A result is reusable only when every required file exists, `status.json` says
`success`, `error.json` has no error, final parsing succeeded, and the expected
semantic cache key matches. For v2 envelopes, the declared internal-call count
must match the preserved call ledger and every declared step must contain all
required per-step files; a required propagated previous-output file must also
exist. Missing or corrupt step evidence classifies the result as `incomplete`
and invalidates reuse. `keep_alive` is recorded but is not part of that semantic
identity.

A declared schema mismatch is reported explicitly as `schema_mismatch`.
Historical schema-v1 evidence is never an automatic schema-v2 cache hit. The
numeric rules and the explicit legacy-admission boundary are specified in
`docs/CACHE_IDENTITY_SCHEMA_V2.md`.

## Stage A2 prompt identity and historical sidecars

For the `stage-a2-v1` identity contract, `prompt_hash` is the SHA-256 of the
approved P1 or P2 source prompt identified by the request metadata. It is not
the P0 baseline prompt hash and it is not the hash of a dataset-specific
assembled prompt bundle. `assembled_prompt_hash` remains a separate artifact
hash. The runtime derives the variant `prompt_hash` once and uses the same
value for cache construction and for both success and failure envelopes.

The planner and runtime must use the same ordered schema-v2 field set. A
variant-hash change invalidates the cache identity. P0 hashes may be retained
only as explicit baseline-linkage metadata; they may never overwrite P1/P2
runtime identity.

Revised Phase 12 is a future-only writer correction. It does not authorize
editing or rekeying historical Stage A2 envelopes. Any historical semantic
admission is recorded in a separate sidecar that preserves the original plan
key, runtime key, typed differences, and decision. A sidecar never converts a
historical strict failure into a cache hit and never replaces raw, normalized,
final, request, response, parse, telemetry, status, or envelope evidence.

## Repair-aware prompt-variant governance

P1/P2 are interventions on the initial ontology-generation prompt. A
procedure-level syntax-repair call is variant-agnostic and is not required to
repeat the P1/P2 suffix when all of the following hold: the initial call used
the approved variant; the repair request exactly follows the frozen repair
builder; it propagates the immediately preceding ontology that failed parsing;
it is explicitly marked as a repair stage; it uses no P0 generation prompt;
and it changes no scenario, CQ order, method, model, generation control,
parser, normalization, or repair policy.

This is an evidence-admission rule, not a change to historical cache identity.
The absence of a variant suffix in a qualifying repair call must remain
explicit in the call ledger and sidecar audit. It must never be represented as
if every internal call contained the suffix, and it does not convert an old
runtime envelope into a matching cache entry.
