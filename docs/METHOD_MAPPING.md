# Generation Method Mapping

The experiment evaluates exactly three method IDs: `ontogenia`, `domain-ontogen`, and `neon-gpt`. Their selection is recorded in `config/course_methods.yaml`; their executable dispatch is implemented in `restapi/ontology_adapter.py`.

| Method ID | Executed behavior | Prompt source | Call structure |
|---|---|---|---|
| `ontogenia` | Memoryless CQ-by-CQ generation; normalized fragments are combined into the task ontology | `datasets/ontology_generation/prompts/ontogenia/P0_original.txt` | One independent call per CQ |
| `domain-ontogen` | Domain-oriented generation using the scenario and one CQ at a time | `datasets/ontology_generation/prompts/domain-ontogen/P0_original.txt` | One independent call per CQ |
| `neon-gpt` | Staged NeOn pipeline with recorded syntax and repair steps | `datasets/ontology_generation/prompts/neon-gpt/P0_original.txt` | Initial generation followed by the method's applicable validation/repair calls |

P1 and P2 are append-only variants of each method's P0 prompt. The approved files are under `datasets/ontology_generation/prompts/<method>/`, and their hashes are recorded in `datasets/ontology_generation/prompts/variant_approval.json` and the frozen A2 configuration.

## Provenance

- Ontogenia/Memoryless CQ-by-CQ prompt: `external_resources/Onto-Generation/PromptingTechniques/README.md`.
- Domain-OntoGen prompt: `external_resources/Domain-OntoGen/README.md`, source commit `894441e367acdbbd1ea662b6f1a6919d13533051`.
- NeOn-GPT prompt: `external_resources/NEON-GPT/gpt_wine_ont_day1/day1_gpt_prompt_list.txt`, source commit `bce7a6a805faa23dc169f691afb5aaaacad3d99d`.

Prompt assembly and internal call order are verified without a real provider by `tests/test_phase3_prompt_assembly.py` and the snapshots under `tests/snapshots/prompts/`.
