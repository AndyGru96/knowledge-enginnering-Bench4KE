# Dataset preparation

## Scope and provenance

The source workbook is `external_resources/Onto-Generation/Dataset_OntoGen/Dataset.xlsx`.
The executable file is `datasets/ontology_generation/normalized/project2_full_generation.jsonl`.
It contains **17 scenarios and 74 CQ records**. Of 112 nonempty source CQs, 38 lack a reliable story link and are excluded; six further source rows are empty. This is the documented executable subset, not all nonempty workbook rows.

Story grouping uses existing StoryIDs, normalized exact matching and actual merged-cell evidence, without guessing missing links. Semicolon-separated IDs require every referenced story to exist. Gold mapping uses normalized CQID-to-filename-stem equality; missing gold does not exclude an otherwise usable CQ.

The 36 included gold modules are unchanged source files; one contains RDF/XML despite its `.ttl` extension. The 74 CQ records include 72 distinct texts. Source IDs and identical gold-file pairs are retained for traceability; the same-scenario duplicate CQ is generated twice, not loaded twice.

In `datasets/ontology_generation/`, `source_row_reconciliation.csv` records inclusion and mapping decisions, `gold_mapping.csv` records gold links, `conversion_errors.csv` records exceptions, and `dataset_audit.json` records scope and source hashes. Repository-owned paths are relative to the project root.

## Rebuild

From the project root, write a separate reconstruction:

```shell
python -m scripts.prepare_ontology_dataset --output-root outputs/prepared_data
```

Omit `--output-root` to replace the prepared data and runtime prompts in their standard locations. Existing gold files are sufficient. Memoryless and Domain-OntoGen prompts are extracted from source material; the NeOn adaptation is copied from its runtime template.

Optional gold extraction uses `--extract-gold`, original course ZIPs beside the workbook, and `GOLD_ARCHIVE_PASSWORD` for encrypted archives. The loader accepts one JSONL file, rejects directories and duplicate dataset IDs, and resolves relative paths from the project root.
