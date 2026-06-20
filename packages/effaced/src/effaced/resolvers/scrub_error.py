"""The :func:`scrub_error` helper — a resolver failure's PII-free shape."""

from __future__ import annotations


def scrub_error(exc: BaseException) -> str:
    """Reduce an exception to a stable, PII-free label: its class name only.

    Resolver failures routinely embed personal data in their *message* — a
    provider raises ``ValueError("no customer for ada@example.com")``, an HTTP
    client echoes the failing identifier. The audit trail and the outbox's
    ``last_error`` are PII-free by construction (CLAUDE.md non-negotiable #4),
    so a failure must be recorded by *what kind* it was, never *what it said*.

    This is the single definition of that PII-free shape, shared by resolver
    authors recording their own failures and by the saga runner when it
    abandons, retries, or signals an entry. It returns ``type(exc).__name__``
    and **nothing else**: never ``str(exc)``, ``exc.args``, the message of a
    chained ``__cause__``/``__context__``, or any attribute a subclass adds.
    The class name is stable across calls and processes for a given exception
    type, so it is safe to compare, group, and persist.

    Args:
        exc: The raised exception. ``BaseException`` is accepted so the same
            helper covers cancellation and other non-:class:`Exception`
            signals the runner may see; only its type is read, never its
            payload.

    Returns:
        The exception's ``type(exc).__name__`` — a non-empty, PII-free class
        label. No part of the message, args, or chained-exception text appears.
    """
    return type(exc).__name__
