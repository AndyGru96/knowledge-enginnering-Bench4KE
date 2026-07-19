# Cache Identity Schema v2

Status: active for future experiment planning and execution. Historical Stage
A1 evidence remains schema v1 and is not rewritten.

## Identity fields and schema marker

Schema v2 hashes a mapping containing `cache_identity_schema_version: 2`
followed by the existing semantic identity fields: dataset, method, provider,
model and digest, prompt, generation options, procedure/ODP, repair policy,
repository, dataset manifest, and experiment configuration. The schema marker
is part of the hashed payload.

Future experiment configurations must explicitly declare:

```yaml
cache_identity_schema_version: 2
```

Planning and runtime code must both call `cache_identity_v2`; schema v1 is
available only through `cache_identity_v1` (and its backwards-compatible
historical alias). A missing or different runtime schema is reported as
`schema_mismatch`, never as a v2 hit.

## Canonicalization algorithm

Values are serialized recursively into deterministic JSON and then encoded as
UTF-8 without an ASCII-escaping pass.

- Mapping keys must be strings and are sorted by Unicode code-point order.
- Arrays preserve element order. Tuples accepted by the Python implementation
  serialize as JSON arrays.
- Strings use JSON escaping while retaining Unicode characters.
- Null serializes as `null`.
- Booleans serialize as `true` or `false` and are handled before integers, so
  they never collapse into `1` or `0`.
- Integers serialize as base-10 integer text.
- Finite floats that are mathematically integral serialize as the same integer
  text. Consequently `0`, `0.0`, and `-0.0` share one representation, as do
  `1` and `1.0`.
- Other finite floats use Python's deterministic shortest round-trip decimal
  representation. Thus `0.3` remains different from
  `0.30000000000000004`.
- NaN and positive or negative infinity are rejected.
- Unsupported scalar types and non-string mapping keys are rejected.

Compact punctuation is fixed: no insignificant whitespace is emitted. The
same supported value therefore yields the same UTF-8 bytes and SHA-256 key on
repeated executions in the frozen Python runtime.

## B8 root cause and compatibility

Schema v1 used ordinary `json.dumps` directly on parser-produced Python
values. JSON integer `0` and JSON float `0.0` are different byte sequences, so
the A1 plan and Pydantic runtime paths produced different SHA-256 keys despite
the same numeric experiment setting.

Schema v2 normalizes the numeric value before hashing and includes its schema
version. It does not change a historical key, relabel an old envelope, or
permit an automatic v1-to-v2 cache hit. Legacy A1 evidence can enter downstream
analysis only through the task-specific
`results/a1_evidence_admission_manifest.json`, which records both preserved v1
keys, independently derived v2 keys, typed differences, request semantics, and
evidence hashes.

## Stage A2 identity contract

Phase 10 defines, but does not execute, the explicit `stage-a2-v1` identity
contract. It uses the same schema-v2 canonical serializer while enumerating
dataset-record hash, prompt variant, normalization policy,
parser/output-contract version, and code identity. Shared
`cache_identity_v2_for_fields` is used by proposed planning and future runtime
envelopes. The approved Phase 9 fixed-field v2 function remains unchanged, so
its 51 admitted semantic keys remain reproducible. No A2 key may be created
until an exact approved P1/P2 prompt hash exists.
