# C3 Statistical Analysis Plan

Status: frozen analysis policy for a future, separately authorized A2 result
set. No P1/P2 result or publication claim is calculated in Phase 10.

## Design and analysis units

The paired unit is one frozen dataset item within one method. Each complete
unit is intended to have P0, P1, and P2 observations generated with identical
model controls. P0 comes only from the admitted A1 evidence; it is never
regenerated for A2. Pairing keys are `(method, dataset_id)` and prompt variant.

Missing generation evidence is not converted into failure. An unparsable but
complete generated result is a parse failure for the binary endpoint. Every
analysis reports its actual paired denominator.

## Primary binary endpoint

The primary endpoint is top-level `final_parse_success` for P0, P1, and P2.
Raw parse success remains separate and is read only from `raw_parse_success`;
normalized or repaired success never substitutes for raw success.

For each method, Cochran's Q is the planned omnibus test when all three paired
binary observations exist. If the usable paired denominator is insufficient,
the result is reported as not estimable rather than pooled across methods.

After an estimable omnibus test, P0/P1, P0/P2, and P1/P2 use McNemar tests.
The exact binomial method is used when total discordance is below 25;
otherwise use the continuity-corrected asymptotic method. Report both
discordant cells, paired denominator, raw p-value, and Holm-adjusted p-value.
Holm correction covers the three comparisons within each method. Report
paired risk difference, matched odds ratio when defined, and confidence
intervals with undefined or infinite cases explicit.

## Primary prompt-sensitivity endpoint

For each parseable paired result, extract the ontology signature below and
calculate `J(P0,P1)` and `J(P0,P2)`. If both sets are empty, Jaccard is 1.0. A
missing or unparsable ontology yields missing Jaccard, not an empty set or zero.

Compare `J(P0,P1)` with `J(P0,P2)` using a paired two-sided Wilcoxon
signed-rank test, separately by method. Both values must exist. Use Pratt
zero-difference handling. Use an exact implementation only when it supports
the observed ties/zeros; otherwise identify the deterministic permutation or
asymptotic implementation. Report pair count, zeros, positive/negative ranks,
statistic, p-value, median paired difference, matched rank-biserial effect
size, and a 95% paired-bootstrap interval using 10,000 resamples and seed 42.

## Exact ontology-term extraction policy

Parse `final_ontology.ttl` with RDFLib using the recorded final format. Terms
are named URI resources that either:

1. are typed `owl:Class`, `rdfs:Class`, `owl:ObjectProperty`,
   `owl:DatatypeProperty`, `owl:AnnotationProperty`, `rdf:Property`, or
   `owl:NamedIndividual`; or
2. occur as named resources in `rdfs:domain`, `rdfs:range`,
   `rdfs:subClassOf`, `owl:equivalentClass`, `owl:disjointWith`, or
   `owl:inverseOf` assertions.

Blank nodes never count as terms, though named resources referenced by their
axioms can count. Exclude the ontology document IRI typed `owl:Ontology` and
all RDF, RDFS, OWL, and XSD built-in namespace terms.

IRI normalization is deterministic: Unicode NFC; lowercase scheme and host;
remove default ports; percent-decode only RFC 3986 unreserved characters;
preserve path/query/fragment case, trailing delimiters, and reserved escapes.
No label, local-name, synonym, namespace-alignment, or blank-node matching is
permitted.

## Missingness, multiplicity, and reporting

- Complete unparseable outputs are binary failures but have missing term-set
  metrics.
- Missing task evidence is excluded and reported, never converted to failure.
- Report method-specific denominators and every exclusion reason.
- C2/LLM-as-judge and OOPS network evaluation are outside this plan.
- Other metrics are exploratory. Apply Benjamini-Hochberg FDR within each
  documented endpoint-by-method test family and report raw/adjusted p-values.
- No publication claim is authorized until complete admitted P1/P2 evidence
  exists and this frozen policy is executed without post-hoc endpoint changes.

