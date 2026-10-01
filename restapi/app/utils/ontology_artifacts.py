"""Atomic records and Turtle parsing of unchanged, separately scoped responses."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from rdflib import BNode, Graph
from rdflib.namespace import OWL, RDF, RDFS

DEFAULT_BASE = "https://bench4ke.invalid/ontology/"


def as_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_text(path: Path, content: str) -> None:
    write_bytes(path, content.encode("utf-8"))


def write_json(path: Path, value: Any) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    rows = list(rows)
    if not rows and fieldnames is None:
        raise ValueError("Cannot write an empty table without columns")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames or list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    write_text(path, buffer.getvalue())


def scenario_base(dataset_id: str) -> str:
    return f"https://bench4ke.invalid/scenarios/{sha256(dataset_id.encode('utf-8'))}/"


def parse_turtle(content: str, base: str = DEFAULT_BASE) -> tuple[Graph | None, str | None]:
    if not content.strip():
        return None, "Empty ontology content"
    try:
        # Explicit @base in the response overrides this common default.
        return Graph().parse(data=content, format="turtle", publicID=base), None
    except Exception as exc:
        return None, str(exc)


def parse_documents(contents: list[str], base: str) -> tuple[Graph | None, str | None]:
    if not contents:
        return None, "No response documents"
    merged = Graph()
    for number, content in enumerate(contents, 1):
        graph, error = parse_turtle(content, base)
        if graph is None:
            return None, f"Response {number}: {error}"
        # Each response is an RDF document with its own blank-node scope.
        nodes = {}
        for triple in graph:
            merged.add(tuple(nodes.setdefault(term, BNode()) if isinstance(term, BNode) else term for term in triple))
    return merged, None


def graph_flags(graph: Graph | None) -> dict:
    if graph is None:
        return {"empty_ontology": None, "has_evaluable_entities": None}
    types = (OWL.Class, RDFS.Class, OWL.ObjectProperty, OWL.DatatypeProperty)
    return {"empty_ontology": len(graph) == 0,
            "has_evaluable_entities": any(any(graph.subjects(RDF.type, kind)) for kind in types)}


def run_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    target = (root / path).resolve()
    if path.is_absolute() or not target.is_relative_to(root.resolve()):
        raise ValueError("Run artifact must be a relative path inside this run")
    return target


def load_task_graph(task: dict, root: Path) -> tuple[Graph | None, str | None]:
    documents = json.loads(task["documents_json"]) if isinstance(task["documents_json"], str) else task["documents_json"]
    contents = [run_file(root, document["path"]).read_bytes().decode("utf-8") for document in documents]
    return parse_documents(contents, scenario_base(task["dataset_id"]))
