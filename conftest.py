"""Workspace pytest bootstrap — Hypothesis profiles for CI and scheduled deep runs.

Profiles are selected with ``--hypothesis-profile=<name>``; without the flag
the Hypothesis default (100 examples) applies, which is the dev-laptop mode.
"""

from __future__ import annotations

from hypothesis import settings

# PR gate: deeper than a laptop run, deadline off — shared CI runners stutter
# enough to make per-example deadlines flaky.
settings.register_profile("ci", max_examples=300, deadline=None, print_blob=True)

# Weekly deep-checks workflow: explore far past the PR gate.
settings.register_profile("deep", max_examples=2500, deadline=None, print_blob=True)
