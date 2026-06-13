"""The root README's python snippets stay syntactically valid and import-true."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import effaced
import effaced_stripe


def _find_readme() -> Path | None:
    """Locate the repo-root README, walking up from this test file.

    The repo root is identified by a sibling ``PROOFS.md`` (present only
    there, never beside the package README) so the walk picks the root
    README with its full snippet set, not ``packages/effaced/README.md``.

    Returns ``None`` when no such root is found on any ancestor — the case
    under mutmut, which copies the test suite into ``mutants/tests/`` and
    shifts the directory tree out from under a fixed-depth path. The README
    snippets are a repo-root artifact unrelated to mutating the core
    modules, so the tests skip there rather than fail the whole run.
    """
    for ancestor in Path(__file__).resolve().parents:
        readme = ancestor / "README.md"
        if readme.is_file() and (ancestor / "PROOFS.md").is_file():
            return readme
    return None


README = _find_readme()
PUBLIC_NAMES = {"effaced": effaced.__all__, "effaced_stripe": effaced_stripe.__all__}


def _python_blocks() -> list[str]:
    if README is None:
        pytest.skip("root README.md not reachable (mutmut copied-tree run)")
    text = README.read_text(encoding="utf-8")
    return re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)


def test_readme_has_python_snippets():
    assert _python_blocks()


def test_readme_python_snippets_compile():
    for index, block in enumerate(_python_blocks()):
        compile(block, f"README.md:python-block-{index}", "exec")


def test_readme_imports_name_only_public_api():
    for block in _python_blocks():
        for node in ast.walk(ast.parse(block)):
            if isinstance(node, ast.ImportFrom) and node.module in PUBLIC_NAMES:
                for alias in node.names:
                    assert alias.name in PUBLIC_NAMES[node.module], (
                        f"README imports {alias.name} from {node.module}, "
                        "which is not in its __all__"
                    )
