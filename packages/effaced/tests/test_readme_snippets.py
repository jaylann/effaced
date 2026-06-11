"""The root README's python snippets stay syntactically valid and import-true."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import effaced
import effaced_stripe

README = Path(__file__).resolve().parents[3] / "README.md"
PUBLIC_NAMES = {"effaced": effaced.__all__, "effaced_stripe": effaced_stripe.__all__}


def _python_blocks() -> list[str]:
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
