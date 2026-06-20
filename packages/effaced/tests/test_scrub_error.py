"""Unit tests for :func:`effaced.scrub_error` — the PII-free error label."""

from __future__ import annotations

import pytest

from effaced import scrub_error

_PII = "ada.lovelace@example.com"


class _ResolverError(Exception):
    """A resolver-style exception that carries PII in its message."""


class _Outer:
    """Namespace holding a nested exception class."""

    class NestedError(Exception):
        """A nested exception — ``__qualname__`` differs from ``__name__``."""


class _ChattyError(Exception):
    """An exception whose ``__str__``/``__repr__`` deliberately leak PII."""

    def __str__(self) -> str:
        return f"delete failed for {_PII}"

    def __repr__(self) -> str:
        return f"_ChattyError: {_PII}"


@pytest.mark.parametrize(
    "exc",
    [
        ValueError(f"no customer for {_PII}"),
        _ResolverError(f"delete failed: subject_id={_PII}, token=sk_live_42"),
        RuntimeError(_PII),
        KeyError(_PII),
    ],
)
def test_returns_only_the_class_name(exc: Exception) -> None:
    """The scrubbed label is the class name and excludes message/args."""
    scrubbed = scrub_error(exc)
    assert scrubbed == type(exc).__name__
    assert _PII not in scrubbed


def test_excludes_args_and_chained_cause() -> None:
    """Neither args nor a chained ``__cause__`` message appears in the label."""
    inner = ValueError(f"inner {_PII}")
    outer = RuntimeError(f"outer {_PII}")
    outer.__cause__ = inner
    scrubbed = scrub_error(outer)
    assert scrubbed == "RuntimeError"
    assert _PII not in scrubbed
    assert "inner" not in scrubbed
    assert "outer" not in scrubbed


def test_uses_bare_name_for_nested_classes() -> None:
    """A nested exception class scrubs to its bare ``__name__``, still PII-free."""
    scrubbed = scrub_error(_Outer.NestedError(_PII))
    assert scrubbed == "NestedError"
    assert _PII not in scrubbed


def test_subclass_with_pii_str_and_repr_still_scrubs_clean() -> None:
    """A subclass whose ``__str__``/``__repr__`` embed PII still scrubs clean.

    This pins the exact promise the docstring makes — a future refactor to
    ``str(exc)``/``repr(exc)`` would leak here and fail loudly.
    """
    scrubbed = scrub_error(_ChattyError())
    assert scrubbed == "_ChattyError"
    assert _PII not in scrubbed


def test_accepts_base_exception() -> None:
    """``BaseException`` (e.g. cancellation) is handled by type, not payload."""
    assert scrub_error(KeyboardInterrupt(_PII)) == "KeyboardInterrupt"
