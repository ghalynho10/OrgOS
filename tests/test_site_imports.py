"""`orgos.site` imports no agent, model library or store driver (AC-28), documented (AC-42)."""

import ast
from pathlib import Path

SITE = Path(__file__).resolve().parents[1] / "src" / "orgos" / "site"
FORBIDDEN = ("orgos.agent", "anthropic", "psycopg", "neo4j", "pymongo", "pydantic_ai")


def _modules() -> list[Path]:
    return sorted(SITE.glob("*.py"))


def test_no_forbidden_import() -> None:
    for path in _modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(FORBIDDEN), f"{path.name} imports {name}"


def test_every_module_and_public_function_has_a_docstring_and_no_bare_except() -> None:
    for path in _modules():
        module = ast.parse(path.read_text(encoding="utf-8"))
        assert ast.get_docstring(module), path.name
        public = [node for node in module.body if not getattr(node, "name", "_").startswith("_")]
        for node in public:
            functions = node.body if isinstance(node, ast.ClassDef) else [node]
            for function in functions:
                if isinstance(function, ast.FunctionDef) and not function.name.startswith("_"):
                    assert ast.get_docstring(function), f"{path.name}: {function.name}"
        for node in ast.walk(module):
            if isinstance(node, ast.ExceptHandler):
                assert node.type is not None, f"{path.name}: bare except"
