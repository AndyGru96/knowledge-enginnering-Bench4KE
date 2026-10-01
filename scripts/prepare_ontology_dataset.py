"""Prepare source-ordered CQs using explicit workbook story links."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
import posixpath
import re
import unicodedata
import zipfile
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence
from xml.etree import ElementTree as ET
from rdflib import Graph
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from restapi.app.utils.ontology_artifacts import write_bytes as atomic_write_bytes, write_text as atomic_write_text, write_json, write_csv as artifact_write_csv, sha256 as sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

@dataclass(frozen=True)
class WorkbookSheet:
    name: str
    max_row: int
    max_col: int
    rows: dict[int, list[Any]]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]) -> None:
    artifact_write_csv(path, list(rows), list(fieldnames))


def column_index(reference: str) -> int:
    letters = re.match(r"[A-Za-z]+", reference)
    if not letters:
        raise ValueError(f"Invalid cell reference: {reference}")
    value = 0
    for char in letters.group(0).upper():
        value = value * 26 + ord(char) - ord("A") + 1
    return value


def _xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.findall(f".//{{{MAIN_NS}}}t")
        )
    value_node = cell.find(f"{{{MAIN_NS}}}v")
    if value_node is None:
        return None
    raw = value_node.text or ""
    if cell_type == "s":
        return shared_strings[int(raw)]
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def read_xlsx(path: Path) -> list[WorkbookSheet]:
    """Read cell values and physical row positions from a simple XLSX file."""

    with zipfile.ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{MAIN_NS}}}si"):
                shared_strings.append(
                    "".join(node.text or "" for node in item.findall(f".//{{{MAIN_NS}}}t"))
                )

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(
            archive.read("xl/_rels/workbook.xml.rels")
        )
        targets = {
            relation.attrib["Id"]: relation.attrib["Target"]
            for relation in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
        }

        sheets: list[WorkbookSheet] = []
        for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
            name = sheet.attrib["name"]
            relationship_id = sheet.attrib[f"{{{DOC_REL_NS}}}id"]
            target = targets[relationship_id].replace("\\", "/")
            if target.startswith("/"):
                archive_path = target.lstrip("/")
            else:
                archive_path = posixpath.normpath(posixpath.join("xl", target))
            sheet_xml = ET.fromstring(archive.read(archive_path))

            # Do not trust worksheet dimensions: formatting commonly expands
            # them to Excel's final row.  Explicit table ranges are reliable
            # source boundaries and also preserve trailing blank table rows.
            table_max_row = 1
            table_max_col = 1
            sheet_rels_path = posixpath.join(
                posixpath.dirname(archive_path),
                "_rels",
                posixpath.basename(archive_path) + ".rels",
            )
            if sheet_rels_path in archive.namelist():
                sheet_relationships = ET.fromstring(archive.read(sheet_rels_path))
                sheet_targets = {
                    relation.attrib["Id"]: relation.attrib["Target"]
                    for relation in sheet_relationships.findall(
                        f"{{{PKG_REL_NS}}}Relationship"
                    )
                }
                for table_part in sheet_xml.findall(f".//{{{MAIN_NS}}}tablePart"):
                    table_id = table_part.attrib.get(f"{{{DOC_REL_NS}}}id")
                    if not table_id or table_id not in sheet_targets:
                        continue
                    table_path = posixpath.normpath(
                        posixpath.join(
                            posixpath.dirname(archive_path), sheet_targets[table_id]
                        )
                    )
                    table_root = ET.fromstring(archive.read(table_path))
                    table_last_ref = table_root.attrib.get("ref", "A1").split(":")[-1]
                    row_match = re.search(r"(\d+)$", table_last_ref)
                    if row_match:
                        table_max_row = max(table_max_row, int(row_match.group(1)))
                    table_max_col = max(table_max_col, column_index(table_last_ref))

            max_row = 1
            max_col = 1

            rows: dict[int, list[Any]] = {}
            for row in sheet_xml.findall(f".//{{{MAIN_NS}}}row"):
                row_number = int(row.attrib["r"])
                cells: dict[int, Any] = {}
                for cell in row.findall(f"{{{MAIN_NS}}}c"):
                    col = column_index(cell.attrib["r"])
                    cells[col] = _xlsx_cell_value(cell, shared_strings)
                    max_col = max(max_col, col)
                rows[row_number] = [cells.get(index) for index in range(1, max_col + 1)]
                max_row = max(max_row, row_number)
            meaningful_rows = [
                row_number
                for row_number, values in rows.items()
                if any(cell_text(value) for value in values)
            ]
            # XLSX dimensions frequently span entire formatted columns.  The
            # meaningful data boundary is the final row containing a value;
            # blank rows inside that boundary remain addressable by source row.
            max_row = max(max(meaningful_rows, default=1), table_max_row)
            max_col = max(max_col, table_max_col)
            sheets.append(
                WorkbookSheet(
                    name=name,
                    max_row=max_row,
                    max_col=max_col,
                    rows=rows,
                )
            )
        return sheets


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def row_dict(sheet: WorkbookSheet, row_number: int) -> dict[str, Any]:
    headers = [cell_text(value) for value in sheet.rows.get(1, [])]
    values = sheet.rows.get(row_number, [])
    return {
        header: values[index] if index < len(values) else None
        for index, header in enumerate(headers)
        if header
    }


def sanitize_filename(value: str, fallback: str = "resource") -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return (normalized or fallback)[:120]


def portable_path(path: Path, repo_root: Path) -> str:
    """Return a stable repository-relative path whenever possible."""

    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(resolved).replace("\\", "/")


def dataset_id_for_story(story_id: str) -> str:
    slug = sanitize_filename(story_id, "story")[:64]
    suffix = hashlib.sha256(story_id.encode("utf-8")).hexdigest()[:10]
    return f"project2_{slug}_{suffix}"


def rdf_format_for_path(path: Path) -> str:
    return {
        ".ttl": "turtle",
        ".rdf": "xml",
        ".owl": "xml",
        ".jsonld": "json-ld",
        ".json-ld": "json-ld",
        ".nt": "nt",
    }.get(path.suffix.lower(), "turtle")


def parse_rdf(path: Path) -> tuple[bool, str | None, int | None, str | None]:
    """Parse RDF with content-aware fallback for mislabeled source files."""

    leading = path.read_bytes()[:512].lstrip().lower()
    extension_format = rdf_format_for_path(path)
    candidates = [extension_format]
    if leading.startswith(b"<?xml") or leading.startswith(b"<rdf:rdf"):
        candidates = ["xml", extension_format]
    elif leading.startswith((b"{", b"[")):
        candidates = ["json-ld", extension_format]
    candidates.extend(["turtle", "xml", "json-ld", "nt"])

    errors: list[str] = []
    for rdf_format in dict.fromkeys(candidates):
        try:
            graph = Graph()
            graph.parse(path, format=rdf_format)
            return True, None, len(graph), rdf_format
        except Exception as exc:  # RDFLib exposes parser-specific errors.
            errors.append(f"{rdf_format}: {exc}")
    return False, " | ".join(errors), None, None


def safe_archive_member(member: str) -> PurePosixPath:
    candidate = PurePosixPath(member)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"Unsafe archive member path: {member}")
    return candidate


def extract_gold_archives(
    dataset_source: Path,
    gold_output: Path,
    password: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    password_bytes = password.encode("utf-8")
    for archive_path in sorted(dataset_source.glob("*.zip")):
        archive_dir = sanitize_filename(archive_path.stem)
        with zipfile.ZipFile(archive_path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                try:
                    member = safe_archive_member(info.filename)
                except ValueError as exc:
                    errors.append(
                        {
                            "category": "unsafe_archive_member",
                            "source": str(archive_path),
                            "identifier": info.filename,
                            "detail": str(exc),
                        }
                    )
                    continue
                if member.suffix.lower() not in {".ttl", ".rdf", ".owl"}:
                    errors.append(
                        {
                            "category": "unsupported_gold_file",
                            "source": str(archive_path),
                            "identifier": info.filename,
                            "detail": "Only .ttl, .rdf, and .owl are extracted.",
                        }
                    )
                    continue
                try:
                    content = archive.read(info, pwd=password_bytes)
                except Exception as exc:
                    errors.append(
                        {
                            "category": "archive_extraction_error",
                            "source": str(archive_path),
                            "identifier": info.filename,
                            "detail": str(exc),
                        }
                    )
                    continue
                output_name = sanitize_filename(member.name, "gold.ttl")
                output_path = gold_output / archive_dir / output_name
                atomic_write_bytes(output_path, content)
                parse_success, parse_error, triple_count, parse_format = parse_rdf(
                    output_path
                )
                records.append(
                    {
                        "archive": archive_path.name,
                        "archive_member": info.filename,
                        "source_sha256": sha256_bytes(content),
                        "filename": output_name,
                        "stem": Path(output_name).stem,
                        "output_path": output_path,
                        "parse_success": parse_success,
                        "parse_error": parse_error,
                        "parse_format": parse_format,
                        "triple_count": triple_count,
                    }
                )
                if not parse_success:
                    errors.append(
                        {
                            "category": "unparseable_gold_file",
                            "source": str(archive_path),
                            "identifier": info.filename,
                            "detail": parse_error or "Unknown RDF parse error",
                        }
                    )
    return records, errors


def extract_memoryless_prompt(markdown_path: Path) -> str:
    text = markdown_path.read_text(encoding="utf-8")
    section_match = re.search(
        r"(?s)^##\s+Memoryless\s+CQbyCQ\s*(.*?)(?=^##\s+Ontogenia\b)",
        text,
        re.MULTILINE,
    )
    if not section_match:
        raise ValueError(f"Memoryless CQbyCQ section not found in {markdown_path}")
    code_match = re.search(r"(?s)```python\s*(.*?)\s*```", section_match.group(1))
    if not code_match:
        raise ValueError(f"Memoryless prompt code block not found in {markdown_path}")
    expression = code_match.group(1).strip()
    try:
        prompt = ast.literal_eval(expression)
    except Exception as exc:
        raise ValueError(f"Memoryless prompt is not one string literal: {exc}") from exc
    if not isinstance(prompt, str):
        raise ValueError("Memoryless prompt literal did not evaluate to text")
    return prompt.rstrip() + "\n"


def extract_domain_ontogen_prompt(markdown_path: Path) -> str:
    """Evaluate the exact published Python string under the named README section."""

    text = markdown_path.read_text(encoding="utf-8")
    section_match = re.search(
        r"(?s)^##\s+Prompt used for ontology generation:\s*(.*?)(?=^##\s+|\Z)",
        text,
        re.MULTILINE,
    )
    if not section_match:
        raise ValueError(
            f"Prompt used for ontology generation section not found in {markdown_path}"
        )
    code_match = re.search(r"(?s)```python\s*(.*?)\s*```", section_match.group(1))
    if not code_match:
        raise ValueError(f"Published Python prompt block not found in {markdown_path}")
    expression = code_match.group(1).strip()
    try:
        prompt = ast.literal_eval(expression)
    except Exception as exc:
        raise ValueError(f"Published prompt is not one Python string literal: {exc}") from exc
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("Published Domain-OntoGen prompt is empty or not text")
    return prompt


def sheet_audit(sheet: WorkbookSheet) -> dict[str, Any]:
    headers = [cell_text(value) for value in sheet.rows.get(1, [])]
    missing = {header: 0 for header in headers if header}
    for row_number in range(2, sheet.max_row + 1):
        row = row_dict(sheet, row_number)
        for header in missing:
            if not cell_text(row.get(header)):
                missing[header] += 1
    return {
        "name": sheet.name,
        "max_row": sheet.max_row,
        "data_row_count": max(0, sheet.max_row - 1),
        "column_names": headers,
        "missing_values_by_column": missing,
    }


def normalize_identifier(value: Any) -> tuple[str, bool]:
    """Normalize workbook identifiers without inferring missing links."""

    if value is None:
        return "", False
    if isinstance(value, float) and value.is_integer():
        return str(int(value)), True
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value), True
    raw = str(value)
    normalized = unicodedata.normalize("NFC", raw.strip()).replace("\r\n", "\n").replace(
        "\r", "\n"
    )
    return normalized, normalized != raw


def normalize_source_text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFC", str(value).strip()).replace(
        "\r\n", "\n"
    ).replace("\r", "\n")


def compress_integer_ranges(values: Sequence[int]) -> list[str]:
    values = sorted(set(values))
    if not values:
        return []
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ranges


def inspect_workbook_with_openpyxl(
    workbook_path: Path,
    cq_max_row: int,
    story_max_row: int,
) -> dict[str, Any]:
    """Inspect Excel structure without using formatting as mapping evidence."""

    workbook = load_workbook(workbook_path, data_only=False, read_only=False)
    result: dict[str, Any] = {
        "library": "openpyxl",
        "openpyxl_inspection_performed": True,
        "sheets": {},
        "cq_story_cells": {},
        "story_id_cells": {},
    }
    boundaries = {"CQs": cq_max_row, "Story": story_max_row}
    relevant_columns = {"CQs": 4, "Story": 2}
    for sheet_name, boundary in boundaries.items():
        worksheet = workbook[sheet_name]
        merged_ranges = sorted(
            (str(item) for item in worksheet.merged_cells.ranges),
            key=str.casefold,
        )
        story_column_merges = sorted(
            (
                str(item)
                for item in worksheet.merged_cells.ranges
                if item.min_col <= 1 <= item.max_col
                and item.max_row >= 2
                and item.min_row <= boundary
            ),
            key=str.casefold,
        )
        hidden_rows = [
            row
            for row in range(2, boundary + 1)
            if bool(worksheet.row_dimensions[row].hidden)
        ]
        hidden_columns = [
            get_column_letter(column)
            for column in range(1, relevant_columns[sheet_name] + 1)
            if bool(worksheet.column_dimensions[get_column_letter(column)].hidden)
        ]
        formulas = []
        for row in worksheet.iter_rows(
            min_row=1,
            max_row=boundary,
            min_col=1,
            max_col=relevant_columns[sheet_name],
        ):
            for cell in row:
                if cell.data_type == "f":
                    formulas.append({"cell": cell.coordinate, "formula": cell.value})
        result["sheets"][sheet_name] = {
            "source_boundary_row": boundary,
            "merged_ranges": merged_ranges,
            "story_id_column_merged_ranges": story_column_merges,
            "hidden_rows": hidden_rows,
            "hidden_row_ranges": compress_integer_ranges(hidden_rows),
            "hidden_columns": hidden_columns,
            "formula_cells": formulas,
        }

    cq_sheet = workbook["CQs"]
    for row_number in range(2, cq_max_row + 1):
        cell = cq_sheet.cell(row_number, 1)
        raw_value = cell.value
        merged_source_range = None
        merged_source_cell = None
        for merged_range in cq_sheet.merged_cells.ranges:
            if merged_range.min_col <= 1 <= merged_range.max_col and (
                merged_range.min_row <= row_number <= merged_range.max_row
            ):
                merged_source_range = str(merged_range)
                merged_source_cell = cq_sheet.cell(
                    merged_range.min_row, merged_range.min_col
                ).coordinate
                if raw_value in (None, ""):
                    raw_value = cq_sheet.cell(
                        merged_range.min_row, merged_range.min_col
                    ).value
                break
        normalized, normalization_applied = normalize_identifier(raw_value)
        result["cq_story_cells"][row_number] = {
            "raw_value": cell.value,
            "raw_value_type": type(cell.value).__name__,
            "normalized_value": normalized,
            "normalization_applied": normalization_applied,
            "merged_source_range": merged_source_range,
            "merged_source_cell": merged_source_cell,
            "merged_cell_propagation_applied": (
                merged_source_range is not None
                and cell.value in (None, "")
                and raw_value not in (None, "")
            ),
            "hidden_row": bool(cq_sheet.row_dimensions[row_number].hidden),
        }

    story_sheet = workbook["Story"]
    for row_number in range(2, story_max_row + 1):
        value = story_sheet.cell(row_number, 1).value
        normalized, normalization_applied = normalize_identifier(value)
        result["story_id_cells"][row_number] = {
            "raw_value": value,
            "raw_value_type": type(value).__name__,
            "normalized_value": normalized,
            "normalization_applied": normalization_applied,
        }
    return result


def build_dataset_records(
    workbook_path: Path,
    gold_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Build explicit full-generation and exact-gold scopes."""

    sheets = read_xlsx(workbook_path)
    by_name = {sheet.name: sheet for sheet in sheets}
    if "CQs" not in by_name or "Story" not in by_name:
        raise ValueError(
            f"Workbook must contain CQs and Story sheets; found {sorted(by_name)}"
        )
    cq_sheet = by_name["CQs"]
    story_sheet = by_name["Story"]
    cq_headers = [cell_text(value) for value in cq_sheet.rows.get(1, [])]
    story_headers = [cell_text(value) for value in story_sheet.rows.get(1, [])]
    required_cq = {"StoryID", "CQID", "CQText", "Category of CQ"}
    required_story = {"StoryID", "StoryText"}
    if not required_cq.issubset(cq_headers):
        raise ValueError(f"CQs sheet missing columns: {sorted(required_cq - set(cq_headers))}")
    if not required_story.issubset(story_headers):
        raise ValueError(
            f"Story sheet missing columns: {sorted(required_story - set(story_headers))}"
        )

    structure = inspect_workbook_with_openpyxl(
        workbook_path, cq_sheet.max_row, story_sheet.max_row
    )
    stories_by_key: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    blank_story_rows: list[int] = []
    for source_row in range(2, story_sheet.max_row + 1):
        source = row_dict(story_sheet, source_row)
        cell_metadata = structure["story_id_cells"][source_row]
        story_id = cell_metadata["normalized_value"]
        story_text = normalize_source_text(source.get("StoryText"))
        if not story_id and not story_text:
            blank_story_rows.append(source_row)
            continue
        if story_id:
            stories_by_key.setdefault(story_id.casefold(), []).append(
                {
                    "story_id": story_id,
                    "story_text": story_text,
                    "source_row": source_row,
                    "normalization_applied": cell_metadata["normalization_applied"],
                    "raw_value_type": cell_metadata["raw_value_type"],
                }
            )

    duplicate_story_ids = [
        {
            "story_id": records[0]["story_id"],
            "source_rows": [record["source_row"] for record in records],
        }
        for records in stories_by_key.values()
        if len(records) > 1
    ]

    raw_cq_rows: list[dict[str, Any]] = []
    for source_row in range(2, cq_sheet.max_row + 1):
        source = row_dict(cq_sheet, source_row)
        story_metadata = structure["cq_story_cells"][source_row]
        cq_id, cq_id_normalized = normalize_identifier(source.get("CQID"))
        raw_cq_rows.append(
            {
                "source_row": source_row,
                "raw_story_id": cell_text(story_metadata["raw_value"]),
                "story_id": story_metadata["normalized_value"],
                "story_cell": story_metadata,
                "cq_id": cq_id,
                "cq_id_normalization_applied": cq_id_normalized,
                "cq_text": normalize_source_text(source.get("CQText")),
                "category": normalize_source_text(source.get("Category of CQ")),
            }
        )

    cq_counts = Counter(
        row["cq_id"].casefold() for row in raw_cq_rows if row["cq_id"]
    )
    duplicate_cq_ids = [
        {
            "cq_id": next(
                row["cq_id"]
                for row in raw_cq_rows
                if row["cq_id"] and row["cq_id"].casefold() == key
            ),
            "source_rows": [
                row["source_row"]
                for row in raw_cq_rows
                if row["cq_id"] and row["cq_id"].casefold() == key
            ],
        }
        for key, count in sorted(cq_counts.items())
        if count > 1
    ]

    gold_by_stem: dict[str, list[dict[str, Any]]] = {}
    for record in gold_records:
        normalized_stem, _ = normalize_identifier(record["stem"])
        gold_by_stem.setdefault(normalized_stem.casefold(), []).append(record)

    exclusion_categories = [
        "empty_cq",
        "duplicate_cq",
        "missing_story_id",
        "story_id_not_found",
        "ambiguous_story_mapping",
        "missing_gold_only",
        "invalid_source_row",
        "other",
    ]
    excluded: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    full_generation_rows: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []
    all_source_cq_ids = {
        row["cq_id"].casefold() for row in raw_cq_rows if row["cq_id"]
    }

    for row in raw_cq_rows:
        story_ids = [
            normalize_identifier(part)[0]
            for part in row["story_id"].split(";")
            if normalize_identifier(part)[0]
        ]
        missing_story_ids = [
            story_id
            for story_id in story_ids
            if story_id.casefold() not in stories_by_key
        ]
        ambiguous_story_ids = [
            story_id
            for story_id in story_ids
            if len(stories_by_key.get(story_id.casefold(), [])) > 1
        ]

        if not row["cq_text"]:
            exclusion_reason = "empty_cq"
        elif not row["cq_id"]:
            exclusion_reason = "invalid_source_row"
        elif cq_counts[row["cq_id"].casefold()] > 1:
            exclusion_reason = "duplicate_cq"
        elif not row["story_id"]:
            exclusion_reason = "missing_story_id"
        elif ambiguous_story_ids:
            exclusion_reason = "ambiguous_story_mapping"
        elif missing_story_ids:
            exclusion_reason = "story_id_not_found"
        else:
            exclusion_reason = ""

        if exclusion_reason:
            mapping_method = "unmapped"
        elif row["story_cell"]["merged_cell_propagation_applied"]:
            mapping_method = "merged_cell_propagation"
        else:
            matched_story_records = [
                stories_by_key[story_id.casefold()][0] for story_id in story_ids
            ]
            normalized_match = (
                row["story_cell"]["normalization_applied"]
                or row["story_cell"]["raw_value_type"] not in {"str", "NoneType"}
                or any(
                    record["normalization_applied"]
                    or record["raw_value_type"] != "str"
                    for record in matched_story_records
                )
            )
            mapping_method = "normalized_exact_id" if normalized_match else "exact_id"

        gold_candidates = (
            gold_by_stem.get(row["cq_id"].casefold(), []) if row["cq_id"] else []
        )
        if len(gold_candidates) == 1:
            gold = gold_candidates[0]
            gold_status = "mapped"
            gold_mapping_method = "exact_id"
            gold_path = str(gold["output_path"])
            gold_sha = gold["source_sha256"]
            gold_parse_success: bool | str = gold["parse_success"]
        elif len(gold_candidates) > 1:
            gold_status = "ambiguous"
            gold_mapping_method = "ambiguous"
            gold_path = "|".join(
                str(candidate["output_path"]) for candidate in gold_candidates
            )
            gold_sha = "|".join(
                candidate["source_sha256"] for candidate in gold_candidates
            )
            gold_parse_success = False
        else:
            gold_status = "missing"
            gold_mapping_method = "unmapped"
            gold_path = ""
            gold_sha = ""
            gold_parse_success = ""

        full_generation_included = not exclusion_reason
        gold_evaluable_included = (
            full_generation_included
            and gold_status == "mapped"
            and gold_parse_success is True
        )
        mapping_row = {
            "source_workbook": workbook_path.name,
            "source_sheet": "CQs",
            "source_row": row["source_row"],
            "raw_story_id": row["raw_story_id"],
            "story_id": row["story_id"],
            "cq_id": row["cq_id"],
            "cq_text_nonempty": str(bool(row["cq_text"])).lower(),
            "hidden_source_row": str(row["story_cell"]["hidden_row"]).lower(),
            "mapping_method": mapping_method,
            "merged_source_range": row["story_cell"]["merged_source_range"] or "",
            "full_generation_included": str(full_generation_included).lower(),
            "included_in_normalized_dataset": str(full_generation_included).lower(),
            "exclusion_reason": exclusion_reason,
            "exclusion_reasons": exclusion_reason,
            "missing_gold_only": str(
                full_generation_included and gold_status == "missing"
            ).lower(),
            "included_in_gold_evaluable_scope": str(gold_evaluable_included).lower(),
            "mapping_rule": "case-insensitive exact CQID-to-filename-stem",
            "gold_mapping_method": gold_mapping_method,
            "mapping_status": gold_status,
            "gold_path": gold_path,
            "gold_sha256": gold_sha,
            "gold_parse_success": gold_parse_success,
        }
        mapping_rows.append(mapping_row)

        story_records = [
            stories_by_key[story_id.casefold()][0]
            for story_id in story_ids
            if story_id.casefold() in stories_by_key
            and len(stories_by_key[story_id.casefold()]) == 1
        ]
        enriched = {
            **row,
            "story_ids": story_ids,
            "story_records": story_records,
            "mapping_method": mapping_method,
            "gold_path": gold_path or None,
            "gold_sha256": gold_sha or None,
            "gold_parse_success": gold_parse_success if gold_path else None,
            "gold_mapping_status": gold_status,
            "gold_evaluable_included": gold_evaluable_included,
        }
        if exclusion_reason:
            excluded_entry = {
                "source_sheet": "CQs",
                "source_row": row["source_row"],
                "story_id": row["story_id"],
                "cq_id": row["cq_id"],
                "exclusion_reason": exclusion_reason,
                "mapping_method": mapping_method,
                "hidden_source_row": row["story_cell"]["hidden_row"],
            }
            excluded.append(excluded_entry)
            errors.append(
                {
                    "category": exclusion_reason,
                    "source": f"{workbook_path.name}:CQs",
                    "identifier": str(row["source_row"]),
                    "detail": "Excluded from full generation scope by explicit source evidence.",
                }
            )
        else:
            full_generation_rows.append(enriched)

    groups: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
    for row in full_generation_rows:
        groups.setdefault(row["story_id"], []).append(row)

    normalized_items: list[dict[str, Any]] = []
    for story_id, rows in groups.items():
        scenario_stories: OrderedDict[str, dict[str, Any]] = OrderedDict()
        for row in rows:
            for story in row["story_records"]:
                scenario_stories.setdefault(story["story_id"], story)
        user_stories = [story["story_text"] for story in scenario_stories.values()]
        scenario = (
            user_stories[0]
            if len(scenario_stories) == 1
            else "\n\n".join(
                f"[{story['story_id']}]\n{story['story_text']}"
                for story in scenario_stories.values()
            )
        )
        cq_metadata = [
            {
                "cq_id": row["cq_id"],
                "cq_text": row["cq_text"],
                "category": row["category"] or None,
                "source_row": row["source_row"],
                "story_mapping_method": row["mapping_method"],
                "gold_module": row["gold_path"],
                "gold_sha256": row["gold_sha256"],
                "gold_parse_success": row["gold_parse_success"],
                "gold_mapping_status": row["gold_mapping_status"],
                "gold_evaluable": row["gold_evaluable_included"],
            }
            for row in rows
        ]
        normalized_items.append(
            {
                "dataset_id": dataset_id_for_story(story_id),
                "scenario_id": story_id,
                "scenario": scenario,
                "competency_questions": [row["cq_text"] for row in rows],
                "user_stories": user_stories,
                "constraints": {"output_format": "ttl"},
                "metadata": {
                    "dataset_scope": "full_generation",
                    "original_story_id": story_id,
                    "original_story_ids": list(scenario_stories),
                    "original_cq_ids": [row["cq_id"] for row in rows],
                    "source_workbook": workbook_path.name,
                    "source_sheet": "CQs",
                    "source_rows": [row["source_row"] for row in rows],
                    "story_source_rows": [
                        story["source_row"] for story in scenario_stories.values()
                    ],
                    "domain_identifier": None,
                    "domain_identifier_source": "not_provided_by_source_workbook",
                    "ontology_identifiers": [row["cq_id"] for row in rows],
                    "cq_records": cq_metadata,
                },
            }
        )

    mapped_gold_paths = {
        Path(row["gold_path"]).resolve()
        for row in mapping_rows
        if row["mapping_status"] == "mapped" and row["gold_path"]
    }
    orphan_modules = [
        {
            "archive": record["archive"],
            "archive_member": record["archive_member"],
            "output_path": str(record["output_path"]),
            "stem": record["stem"],
            "reason": (
                "No CQID with a normalized case-insensitive exact stem match"
                if record["stem"].casefold() not in all_source_cq_ids
                else "Matched CQ exists but mapping was not unique"
            ),
        }
        for record in gold_records
        if record["output_path"].resolve() not in mapped_gold_paths
    ]

    exclusion_counts = Counter(
        entry["exclusion_reason"] for entry in excluded
    )
    mapping_method_counts = Counter(row["mapping_method"] for row in mapping_rows)
    structure_summary = {
        key: value
        for key, value in structure.items()
        if key not in {"cq_story_cells", "story_id_cells"}
    }
    identifier_cells = [
        *structure["cq_story_cells"].values(),
        *structure["story_id_cells"].values(),
    ]
    string_identifier_values = [
        metadata["raw_value"]
        for metadata in identifier_cells
        if isinstance(metadata["raw_value"], str)
    ]
    case_only_match_count = 0
    for row in full_generation_rows:
        for story_id in row["story_ids"]:
            matched = stories_by_key[story_id.casefold()][0]["story_id"]
            if story_id != matched and story_id.casefold() == matched.casefold():
                case_only_match_count += 1
    normalization_inspection = {
        "normalization_policy": [
            "trim_surrounding_whitespace",
            "Unicode_NFC",
            "integer_numeric_to_canonical_text",
            "normalize_line_endings",
            "case_insensitive_exact_comparison",
        ],
        "story_id_cells_with_surrounding_whitespace": sum(
            value != value.strip() for value in string_identifier_values
        ),
        "story_id_cells_changed_by_unicode_nfc": sum(
            unicodedata.normalize("NFC", value) != value
            for value in string_identifier_values
        ),
        "story_id_cells_with_non_lf_line_endings": sum(
            "\r" in value for value in string_identifier_values
        ),
        "numeric_integer_story_id_cells": sum(
            metadata["raw_value_type"] == "int" for metadata in identifier_cells
        ),
        "integer_like_float_story_id_cells": sum(
            metadata["raw_value_type"] == "float"
            and isinstance(metadata["raw_value"], float)
            and metadata["raw_value"].is_integer()
            for metadata in identifier_cells
        ),
        "cq_id_representation_normalizations": sum(
            row["cq_id_normalization_applied"] for row in raw_cq_rows
        ),
        "case_only_story_matches": case_only_match_count,
        "fuzzy_or_semantic_matches": 0,
    }
    gold_evaluable_count = sum(
        row["included_in_gold_evaluable_scope"] == "true" for row in mapping_rows
    )
    audit = {
        "source_workbooks": [
            {"path": str(workbook_path), "sha256": sha256_file(workbook_path)}
        ],
        "sheets": [sheet_audit(sheet) for sheet in sheets],
        "workbook_structure_audit": structure_summary,
        "normalization_inspection": normalization_inspection,
        "source_row_count": max(0, cq_sheet.max_row - 1),
        "story_count": sum(len(records) for records in stories_by_key.values()),
        "cq_count": sum(1 for row in raw_cq_rows if row["cq_id"] and row["cq_text"]),
        "normalized_item_count": len(normalized_items),
        "normalized_cq_count": sum(
            len(item["competency_questions"]) for item in normalized_items
        ),
        "full_generation_scenario_count": len(normalized_items),
        "full_generation_cq_count": sum(
            len(item["competency_questions"]) for item in normalized_items
        ),
        "gold_evaluable_cq_count": gold_evaluable_count,
        "gold_module_count": len(gold_records),
        "exclusion_counts_by_reason": {
            category: exclusion_counts.get(category, 0)
            for category in exclusion_categories
        },
        "mapping_method_counts": {
            method: mapping_method_counts.get(method, 0)
            for method in [
                "exact_id",
                "normalized_exact_id",
                "merged_cell_propagation",
                "unmapped",
            ]
        },
        "missing_gold_count": sum(
            bool(row["cq_id"] and row["mapping_status"] == "missing")
            for row in mapping_rows
        ),
        "missing_story_count": sum(
            bool(row["cq_id"] and row["cq_text"] and not row["story_id"])
            for row in raw_cq_rows
        ),
        "unresolved_rows": excluded,
        "missing_gold_excludes_full_generation_count": sum(
            row["missing_gold_only"] == "true"
            and row["full_generation_included"] != "true"
            for row in mapping_rows
        ),
        "duplicate_ids": {
            "cq_ids": duplicate_cq_ids,
            "story_ids": duplicate_story_ids,
        },
        "missing_ids": {
            "cq_id_source_rows": [row["source_row"] for row in raw_cq_rows if not row["cq_id"]],
            "story_id_source_rows": [row["source_row"] for row in raw_cq_rows if not row["story_id"]],
        },
        "empty_cqs": [row["source_row"] for row in raw_cq_rows if not row["cq_text"]],
        "missing_stories": [
            {
                "source_row": row["source_row"],
                "cq_id": row["cq_id"],
                "story_id": row["story_id"],
            }
            for row in raw_cq_rows
            if row["cq_id"] and row["cq_text"] and not row["story_id"]
        ],
        "blank_story_rows": blank_story_rows,
        "missing_mappings": [
            {
                "source_row": row["source_row"],
                "story_id": row["story_id"],
                "cq_id": row["cq_id"],
            }
            for row in mapping_rows
            if row["cq_id"] and row["mapping_status"] == "missing"
        ],
        "orphan_modules": orphan_modules,
        "excluded_rows_and_reasons": excluded,
        "domain_identifier_column_present": False,
        "full_generation_scope": (
            "Every non-empty, non-duplicate CQ associated with story text by "
            "exact, normalized-exact, or merged-cell source evidence. Gold is optional."
        ),
        "gold_evaluable_scope": (
            "Rows in full generation scope with one parseable exact CQID-to-gold-stem match; "
            "source_row_reconciliation.csv identifies the subset."
        ),
        "gold_mapping_rule": (
            "Dataset README states module filename is CQID.ttl; matching uses "
            "normalized, case-insensitive exact filename stem equality."
        ),
    }
    return normalized_items, mapping_rows, audit, errors


def existing_gold_records(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".ttl", ".rdf", ".owl"}:
            success, error, triples, rdf_format = parse_rdf(path)
            records.append({
                "archive": path.parent.name, "archive_member": path.name,
                "source_sha256": sha256_file(path), "filename": path.name, "stem": path.stem,
                "output_path": path, "parse_success": success, "parse_error": error,
                "parse_format": rdf_format, "triple_count": triples,
            })
    return records


def relative_paths(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: relative_paths(child, root) for key, child in value.items()}
    if isinstance(value, list):
        return [relative_paths(child, root) for child in value]
    if isinstance(value, Path):
        return portable_path(value, root)
    if isinstance(value, str) and value.startswith(str(root)):
        return portable_path(Path(value), root)
    return value


def prepare_prompts(directory: Path) -> None:
    import yaml
    from restapi.ontology_adapter import OUTPUT_PROTOCOL

    canonical = ROOT / "datasets/ontology_generation/prompts"
    settings = yaml.safe_load((ROOT / "config/course_methods.yaml").read_text(encoding="utf-8"))
    sources = {
        "ontogenia": extract_memoryless_prompt(ROOT / "external_resources/Onto-Generation/PromptingTechniques/README.md"),
        "domain-ontogen": extract_domain_ontogen_prompt(ROOT / "external_resources/Domain-OntoGen/README.md"),
        "neon-gpt": (canonical / "neon-gpt/P0_original.txt").read_bytes().decode("utf-8").split("\n\nOutput protocol:\n\n", 1)[0],
    }
    filenames = {"P0": "P0_original.txt", "P1": "P1_candidate.txt", "P2": "P2_candidate.txt"}
    for method, base in sources.items():
        for variant, filename in filenames.items():
            text = base + "\n\nOutput protocol:\n\n" + OUTPUT_PROTOCOL
            if variant != "P0":
                text += "\n\nAdditional instruction:\n\n" + settings["prompt_variant_instructions"][variant]
            atomic_write_text(directory / method / filename, text)


def prepare_dataset(output_root: Path, *, extract_gold: bool = False) -> dict[str, Any]:
    source = ROOT / "external_resources/Onto-Generation/Dataset_OntoGen"
    gold_directory = ROOT / "datasets/ontology_generation/gold"
    errors = []
    if extract_gold:
        if not list(source.glob("*.zip")):
            raise FileNotFoundError("Place the course gold ZIP archives beside Dataset.xlsx first")
        records, errors = extract_gold_archives(
            source, gold_directory, os.getenv("GOLD_ARCHIVE_PASSWORD", "")
        )
    else:
        records = existing_gold_records(gold_directory)
    items, mapping, audit, workbook_errors = build_dataset_records(source / "Dataset.xlsx", records)
    errors.extend(workbook_errors)
    items = relative_paths(items, ROOT)
    mapping = relative_paths(mapping, ROOT)
    errors = relative_paths(errors, ROOT)
    atomic_write_text(
        output_root / "normalized/project2_full_generation.jsonl",
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in items),
    )
    write_csv(output_root / "source_row_reconciliation.csv", list(mapping[0]), mapping)
    gold_fields = ["source_row", "cq_id", "story_id", "gold_path", "gold_sha256", "gold_parse_success", "mapping_status", "gold_mapping_method"]
    write_csv(output_root / "gold_mapping.csv", gold_fields, [{key: row.get(key) for key in gold_fields} for row in mapping])
    write_csv(output_root / "conversion_errors.csv", ["category", "source", "identifier", "detail"], errors)
    sources = [
        source / "Dataset.xlsx",
        ROOT / "external_resources/Onto-Generation/PromptingTechniques/README.md",
        ROOT / "external_resources/Domain-OntoGen/README.md",
        ROOT / "external_resources/NEON-GPT/gpt_wine_ont_day1/day1_gpt_prompt_list.txt",
        ROOT / "datasets/ontology_generation/raw/ontogenia/memoryless_cqbycq_prompt.txt",
        ROOT / "datasets/ontology_generation/raw/domain-ontogen/prompt.txt",
        ROOT / "datasets/ontology_generation/raw/neon-gpt/day1_gpt_prompt_list.txt",
    ]
    audit["sources"] = [
        {"path": portable_path(path, ROOT), "sha256": sha256_file(path)} for path in sources
    ]
    write_json(output_root / "dataset_audit.json", relative_paths(audit, ROOT))
    prepare_prompts(output_root / "prompts")
    return {
        "scenarios": len(items), "cqs": sum(len(item["competency_questions"]) for item in items),
        "gold_modules": len(records), "excluded_rows": audit["exclusion_counts_by_reason"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "datasets/ontology_generation")
    parser.add_argument("--extract-gold", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare_dataset(args.output_root.resolve(), extract_gold=args.extract_gold), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
