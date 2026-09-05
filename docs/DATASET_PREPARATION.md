# Dataset and Prompt Preparation

## Reproduce

The submitted repository contains the authoritative `Dataset.xlsx`, the
prepared normalized JSONL files, and the extracted gold ontology modules. The
two password-protected ZIP archives supplied with the course material are not
redistributed in this repository.

To reproduce the complete prepared dataset, first place those two original ZIP
archives beside `Dataset.xlsx` under
`external_resources/Onto-Generation/Dataset_OntoGen`. Then use the project
environment and run:

```powershell
.venv\Scripts\python.exe scripts\prepare_ontology_dataset.py
```

The script reads the workbook and any ZIP archives found in that directory.
The archive password is the one supplied with the course material. Running the
script without the two archives can rebuild the workbook-derived records and
prompts, but it cannot reproduce the submitted gold-module set. Output
replacement is staged and atomic at the managed-directory level.

## Relationship and mapping rules

- Workbook source row numbers are retained.
- CQs are grouped only by an explicit `StoryID` that exists in the `Story`
  sheet. Semicolon-separated IDs are accepted only if every referenced ID
  exists.
- Blank rows and CQs without `StoryID` are excluded with reasons; IDs are never
  reconstructed from CQ prefixes.
- Excel merged-cell propagation is allowed only when the `StoryID` cell is
  inside a real merged range whose top-left cell contains the ID.
- Identifier normalization is limited to trimming, NFC Unicode normalization,
  line-ending normalization, integer-like numeric canonicalization, and
  case-insensitive exact comparison.
- Gold mapping follows the dataset README rule: case-insensitive exact equality
  between `CQID` and the module filename stem. Missing and orphan mappings are
  reported without repair. Missing gold never excludes an otherwise valid
  full-generation CQ.
- RDF parsing is content-aware because one source `.ttl` is RDF/XML. The
  detected parse format is recorded per gold file.

## Prepared dataset

The source boundaries are `CQs!A1:D119` and `Story!A1:B38`. The preparation
observed 118 CQ source rows, 112 non-empty CQs, 35 non-empty stories, and 36
gold modules.

The openpyxl structural pass found no merged range in either sheet's
`StoryID` column. The only merged ranges are `CQs!L73:M73`, `L87:M87`, and
`L105:M105`; they are unrelated auxiliary cells. There are no formulas or
hidden relevant columns. Rows 2–17, 29–50, and 53–58 are hidden. The first two
groups contain the 38 non-empty CQs with no explicit `StoryID`; the final group
contains six empty CQs. Therefore no previously excluded row can be recovered
through merged-cell propagation.

### Full-generation scope

`datasets/ontology_generation/normalized/project2_full_generation.jsonl`
contains 17 scenarios and 74 CQs. The backward-compatible `project2.jsonl` is
byte-identical. Gold availability is not an inclusion requirement.

### Gold-evaluable scope

`gold_mapping.csv` marks 27 full-generation CQs as
`included_in_gold_evaluable_scope=true`. The remaining 85 non-empty source CQs
have no exact gold filename-stem match; none is excluded from full generation
for that reason.

### Exclusion reasons

| Reason | Count |
|---|---:|
| `empty_cq` | 6 |
| `missing_story_id` | 38 |
| `duplicate_cq` | 0 |
| `story_id_not_found` | 0 |
| `ambiguous_story_mapping` | 0 |
| `missing_gold_only` | 0 |
| `invalid_source_row` | 0 |
| `other` | 0 |

### Story mapping methods

| Mapping method | Count |
|---|---:|
| `exact_id` | 58 |
| `normalized_exact_id` | 16 |
| `merged_cell_propagation` | 0 |
| `unmapped` | 44 |

All 118 source rows are recorded individually in
`source_row_reconciliation.csv`. The exact current result and exceptions live
in:

- `datasets/ontology_generation/dataset_audit.json`
- `datasets/ontology_generation/gold_mapping.csv`
- `datasets/ontology_generation/source_row_reconciliation.csv`
- `datasets/ontology_generation/conversion_errors.csv`

The loader accepts `project2_full_generation.jsonl`.

## Authoritative prompt recovery

All three approved methods now have runtime prompt resources.

- Domain-OntoGen: the Python string under “Prompt used for ontology generation”
  was extracted from the public companion README. Markdown fences and Python
  delimiters were removed and Python escapes decoded. `{CQ}` and `{OS}` each
  occur once, and the final `O:` is retained.
- NeOn-GPT: `gpt_wine_ont_day1/day1_gpt_prompt_list.txt` was copied from the
  public companion resource without text changes.

The earlier blocker was caused by an incomplete authoritative resource
inventory, not by unavailable published prompts.
