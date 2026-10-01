import ast


from restapi.app.config import ROOT


def test_source_has_no_repeated_module_level_function_definitions():
    for directory in ("restapi", "scripts"):
        for file in (ROOT / directory).rglob("*.py"):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            names = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            assert len(names) == len(set(names)), file

