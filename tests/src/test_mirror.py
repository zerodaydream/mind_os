"""Optional per-module tests under ``tests/backend`` should mirror ``backend`` layout."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "backend"
_TESTS = _ROOT / "tests" / "backend"

_SKIP_PREFIXES = ("worker/",)  # worker is a separate process; tests stay at tests/unit for now


def _has_code(path: Path) -> bool:
    body = ast.parse(path.read_text()).body
    return any(
        not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
        for node in body
    )


def _module_name(relative: Path) -> str:
    parts = list(relative.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
        return "backend." + ".".join(parts) if parts else "backend"
    stem = parts[-1][:-3]
    parts[-1] = stem
    return "backend." + ".".join(parts)


def _expected_tests() -> dict[Path, Path]:
    expected: dict[Path, Path] = {}
    for module in _SRC.rglob("*.py"):
        relative = module.relative_to(_SRC)
        rel_posix = relative.as_posix()
        if any(rel_posix.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if module.name == "__init__.py":
            if not _has_code(module):
                continue
            test_name = "test_init.py"
        else:
            test_name = f"test_{module.name}"
        expected[relative.parent / test_name] = relative
    return expected


def _actual_tests() -> set[Path]:
    if not _TESTS.is_dir():
        return set()
    return {path.relative_to(_TESTS) for path in _TESTS.rglob("test_*.py")}


def test_all_src_modules_import() -> None:
    failures: list[str] = []
    for module in sorted(_SRC.rglob("*.py")):
        rel = module.relative_to(_SRC)
        if any(rel.as_posix().startswith(p) for p in _SKIP_PREFIXES):
            continue
        if module.name == "__init__.py" and not _has_code(module):
            continue
        name = _module_name(rel)
        try:
            importlib.import_module(name)
        except Exception as exc:  # pragma: no cover - report path + cause
            failures.append(f"{name}: {exc}")
    assert not failures, failures[:10]


def test_mirror_paths_documented() -> None:
    """Per-file mirror tree is optional; pytest needs unique basenames (see test_all_src_modules_import)."""
    expected = _expected_tests()
    actual = _actual_tests()
    if _TESTS.is_dir() and actual:
        missing = sorted(
            str(module) for test, module in expected.items() if test not in actual
        )
        assert not missing, f"Modules without mirrored tests: {missing[:20]}"
