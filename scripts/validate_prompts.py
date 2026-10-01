"""Check the nine named prompt templates without calling a model."""

import json

import yaml

from restapi.app.config import DATASET, METHODS, ROOT, VARIANTS
from restapi.ontology_adapter import assemble_prompts, load_template
from restapi.app.utils.ontology_dataset import load_ontology_items


def main() -> int:
    settings = yaml.safe_load((ROOT / "config/course_methods.yaml").read_text(encoding="utf-8"))
    if tuple(settings["methods"]) != METHODS:
        raise ValueError("Course method configuration disagrees with the supported methods")
    for method, entry in settings["methods"].items():
        expected = f"datasets/ontology_generation/prompts/{method}/P0_original.txt"
        if entry["prompt"] != expected or not (ROOT / entry["implementation"]).is_file():
            raise ValueError(f"Invalid configured method files: {method}")
    item = load_ontology_items(DATASET)[0]
    rows = []
    for method in METHODS:
        for variant in VARIANTS:
            template, digest = load_template(method, variant)
            prompts = assemble_prompts(method, item, template)
            expected = 10 if method == "neon-gpt" else len(item.competency_questions)
            if len(prompts) != expected:
                raise ValueError(f"Unexpected prompt count: {method}/{variant}")
            rows.append({"method": method, "variant": variant, "calls": len(prompts), "sha256": digest})
    print(json.dumps({"templates_checked": len(rows), "templates": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
