# Runs and evaluation


## Commands

Install dependencies and start Ollama as described in the [README](../README.md). Run commands from the project root.

Offline checks use simulated responses, not model inference:

```shell
python -m pytest -q
python -m compileall -q restapi scripts tests
python -m scripts.validate_prompts
```

For data reconstruction, see [DATASET_PREPARATION.md](DATASET_PREPARATION.md).
Set an installed model and generation settings in `config/course_methods.yaml`, then run:

```shell
python -m restapi.ontology_adapter --config config/course_methods.yaml
```

Use the returned run directory and a **new, previously unused analysis directory**:

```shell
python -m scripts.analyze_prompt_sensitivity --run-dir outputs/RUN_ID --output-dir results/RUN_ID
```

Replace `RUN_ID` with the actual identifier. This command computes both C2 and C3; use `scripts.analyze_documentation_completeness` with the same arguments for C2 alone. Do not overwrite the retained result tables or reuse an analysis directory: absent outputs can leave older tables behind. `--allow-partial` permits unfinished runs with explicit missing-task markers.

## Metric definitions

**Parseability.** Every designated ontology response must parse directly as Turtle, using the document rules in [METHOD_MAPPING.md](METHOD_MAPPING.md). No code is extracted from prose or fences. Complete invalid responses count as parse failures; incomplete generation is missing. Empty, malformed, `done=false` and length-truncated model responses are rejected, with received HTTP evidence retained. Zero-triple graphs and missing evaluable entities are flagged separately.

**C2.** Denominators use explicitly declared classes, object properties and datatype properties. Labels use `rdfs:label` or `skos:prefLabel`; documentation uses `rdfs:comment`, `skos:definition`, `dcterms:description` or `schema:description`. Only nonempty literals count. Nontrivial documentation requires at least 20 characters and three whitespace-delimited words. Ontology-header documentation requires a nonempty label or description on an `owl:Ontology` subject; imports alone do not qualify. Zero denominators stay unavailable. Coverage summaries pool known entity counts and separately report task availability and empty outputs.

**C3.** Comparisons pair method and dataset ID within one run. Cochran's Q requires three complete variants; McNemar uses each selected pair independently, with Holm correction within method. Term Jaccard compares exact resolved RDF IRIs of declared classes, properties, named individuals and selected class/property relations. Built-in vocabulary, ontology document IRIs and blank nodes are excluded; no further URL normalization is applied. Unparseable outputs are missing, not empty sets. Empty term sets cannot establish stability and are excluded from Wilcoxon comparisons.

Pratt Wilcoxon compares `J(P0,P1)` with `J(P0,P2)` on complete eligible triples, not either variant against P0. Effect sizes and median-difference bootstrap intervals use the policy in `config/c3_analysis_policy.yaml` (10,000 resamples, seed 42).
