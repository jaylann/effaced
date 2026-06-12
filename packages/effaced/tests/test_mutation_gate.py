"""The mutation-gate script's parse/decide logic (``scripts/check_mutation_gate.py``).

Unit-tests the pure functions that turn ``mutmut results`` output plus the
equivalent-mutant allowlist into a pass/fail decision — the heart of flipping
the weekly mutation job to a hard gate (#124). The script lives outside the
package (mirroring ``scripts/check_file_length.py``), so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest


def _find_script() -> Path | None:
    """Locate ``scripts/check_mutation_gate.py``, walking up from this file.

    Returns ``None`` under mutmut, which copies the suite into
    ``mutants/tests/`` and shifts the tree out from under a fixed-depth
    path; the gate script is a repo-root tool unrelated to mutating the
    core modules, so these tests skip there rather than error the run.
    """
    for ancestor in Path(__file__).resolve().parents:
        candidate = ancestor / "scripts" / "check_mutation_gate.py"
        if candidate.is_file():
            return candidate
    return None


def _load_gate() -> ModuleType:
    script = _find_script()
    if script is None:
        pytest.skip(
            "scripts/check_mutation_gate.py not reachable (mutmut copied-tree run)",
            allow_module_level=True,
        )
    spec = importlib.util.spec_from_file_location("check_mutation_gate", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's dataclasses can resolve their
    # postponed (``from __future__ import annotations``) field types via
    # ``sys.modules[cls.__module__]``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def test_parse_results_groups_by_status() -> None:
    text = (
        "To apply a mutant on disk: ...\n"
        "\n"
        "effaced.saga.outbox.x__row__mutmut_1: survived\n"
        "    effaced.saga.runner.xǁSagaRunnerǁ__init____mutmut_1: no tests\n"
        "effaced.audit.event.x__foo__mutmut_2: timeout\n"
        "effaced.audit.event.x__bar__mutmut_3: suspicious\n"
        "effaced.audit.event.x__baz__mutmut_4: killed\n"
    )
    survivors = gate.parse_results(text)
    assert survivors.failing == [
        "effaced.saga.outbox.x__row__mutmut_1",
        "effaced.saga.runner.xǁSagaRunnerǁ__init____mutmut_1",
    ]
    assert survivors.warnings == [
        "effaced.audit.event.x__foo__mutmut_2",
        "effaced.audit.event.x__bar__mutmut_3",
    ]


def test_parse_results_skips_noise_lines() -> None:
    survivors = gate.parse_results("\n  \nGenerating mutants\n---\n")
    assert survivors.failing == []
    assert survivors.warnings == []


def test_parse_allowlist_strips_comments_and_dedupes() -> None:
    text = (
        "# a comment\n"
        "\n"
        "effaced.saga.outbox.x__row__mutmut_1  # trailing reason\n"
        "effaced.saga.outbox.x__row__mutmut_1\n"
        "effaced.manifest.data_map.xǁDataMapǁto_payload__mutmut_1\n"
    )
    assert gate.parse_allowlist(text) == (
        "effaced.saga.outbox.x__row__mutmut_1",
        "effaced.manifest.data_map.xǁDataMapǁto_payload__mutmut_1",
    )


def test_decide_passes_when_survivors_exactly_match_allowlist() -> None:
    survivors = gate.parse_results("a.b.x__m__mutmut_1: survived\n")
    decision = gate.decide(survivors, ("a.b.x__m__mutmut_1",))
    assert decision.ok
    assert decision.unexpected == ()
    assert decision.stale == ()


def test_decide_fails_on_unallowlisted_survivor() -> None:
    survivors = gate.parse_results("a.b.x__new__mutmut_1: survived\n")
    decision = gate.decide(survivors, ())
    assert not decision.ok
    assert decision.unexpected == ("a.b.x__new__mutmut_1",)


def test_decide_fails_on_unallowlisted_no_tests() -> None:
    survivors = gate.parse_results("a.b.x__new__mutmut_1: no tests\n")
    decision = gate.decide(survivors, ())
    assert not decision.ok
    assert decision.unexpected == ("a.b.x__new__mutmut_1",)


def test_decide_fails_loudly_on_stale_allowlist_entry() -> None:
    survivors = gate.parse_results("")  # nothing survived this run
    decision = gate.decide(survivors, ("a.b.x__gone__mutmut_1",))
    assert not decision.ok
    assert decision.stale == ("a.b.x__gone__mutmut_1",)


def test_decide_reports_warnings_without_failing() -> None:
    survivors = gate.parse_results("a.b.x__slow__mutmut_1: timeout\n")
    decision = gate.decide(survivors, ())
    assert decision.ok
    assert decision.warnings == ("a.b.x__slow__mutmut_1",)


def test_render_names_each_problem_class() -> None:
    decision = gate.GateDecision(
        unexpected=("a.b.x__new__mutmut_1",),
        stale=("a.b.x__gone__mutmut_1",),
        warnings=("a.b.x__slow__mutmut_1",),
    )
    out = gate.render(decision)
    assert "not on the equivalent-mutant allowlist" in out
    assert "stale allowlist entries" in out
    assert "a.b.x__new__mutmut_1" in out
    assert "a.b.x__gone__mutmut_1" in out


@pytest.mark.parametrize(
    ("results", "allowlist", "expected_exit"),
    [
        ("a.b.x__m__mutmut_1: survived\n", "a.b.x__m__mutmut_1\n", 0),
        ("a.b.x__new__mutmut_1: survived\n", "", 1),
        ("", "a.b.x__gone__mutmut_1\n", 1),
    ],
)
def test_decision_ok_maps_to_exit_code(results: str, allowlist: str, expected_exit: int) -> None:
    decision = gate.decide(gate.parse_results(results), gate.parse_allowlist(allowlist))
    assert (0 if decision.ok else 1) == expected_exit
