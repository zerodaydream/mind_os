"""Source-level checks that complement the import-linter contracts."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "opsagent"
PROVIDER_MODULES = {"langchain_google_genai", "google", "googleapiclient", "msal"}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_provider_sdks_are_imported_only_inside_integrations() -> None:
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if "integrations" not in p.parts and _imports(p) & PROVIDER_MODULES
    ]
    assert offenders == []


def test_no_module_level_mutable_state_in_domain_and_application() -> None:
    offenders: list[str] = []
    for layer in ("domain", "application"):
        for path in (SRC / layer).rglob("*.py"):
            for node in ast.parse(path.read_text()).body:
                value = getattr(node, "value", None)
                targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
                exported = any(getattr(t, "id", "") == "__all__" for t in targets)
                if (
                    isinstance(node, ast.Assign | ast.AnnAssign)
                    and isinstance(value, ast.List | ast.Dict | ast.Set)
                    and not exported
                ):
                    offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert offenders == []
