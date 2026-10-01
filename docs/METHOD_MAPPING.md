# Generation methods

These definitions describe the current implementation in `restapi/ontology_adapter.py`, not the earlier experiment retained under `report/` and `results/`. Equivalence to a specific instructor-issued baseline has not been established.

| Method ID | Execution | Ontology documents |
|---|---|---|
| `ontogenia` | Memoryless: one independent call per CQ and story; previous RDF is empty | Every CQ response |
| `domain-ontogen` | One independent call per CQ and story | Every CQ response |
| `neon-gpt` | Ten ordered calls carrying preceding conversation history | Responses from steps 5–10 |

`ontogenia` is not metacognitive Ontogenia. Its prompt comes from `external_resources/Onto-Generation/PromptingTechniques/README.md`; Domain-OntoGen uses `external_resources/Domain-OntoGen/README.md`.

NeOn uses the source list in `external_resources/NEON-GPT/gpt_wine_ont_day1/day1_gpt_prompt_list.txt`, adapted to the supplied scenario rather than wine. Steps 1–4 produce planning text using the fixed dataset CQs. Step 5 produces an initial ontology; steps 6–10 add independently valid Turtle documents. All ten responses are retained.

## Prompt variants

Templates remain under `datasets/ontology_generation/prompts/<method>/` as `P0_original.txt`, `P1_candidate.txt` and `P2_candidate.txt`.

Despite its filename, P0 is an **adapted reference**: all ontology-producing prompts require standalone Turtle, explanations only as Turtle comments, and no Markdown fences. P1 adds serialization instructions; P2 adds silent syntax-check instructions. For NeOn, these instructions apply to steps 5–10 only.

This measures instruction-suffix sensitivity, not unrestricted semantic rephrasing. Each task saves the template/hash; each call saves the assembled prompt and full request, including conversation history. No full model experiment has been rerun for this implementation.

## Output handling

Responses are preserved without cleaning or repair. Ontology responses are parsed separately, sharing a scenario-derived default base while respecting explicit `@base`. Blank nodes retain document-local scope during graph assembly. `ontology_transcript.txt` is a joined record, not the graph-merge input. C2/C3 read the separate response documents.
