"""Adversarial fuzz proof of the composite-key canonical serialization.

The canonical form (:func:`effaced.canonical_subject_id` /
:func:`effaced.parse_canonical`, ADR 0025) is the single string that the
audit trail, the transactional outbox, and SQL all key a multi-column
subject by. A *collision* — two distinct subject keys serializing to the
same string — would let an erasure or export for subject A land on subject
B's data: the worst failure this library can have. The example test in
``test_composite_subject_properties.py`` pins the headline
``("a", "b:c")`` vs ``("a:b", "c")`` case; this module is the generated
evidence that no adversarial input defeats the escaping.

The strategies are deliberately hostile to the escape machinery: every
element is drawn heavy on the reserved separator (``\\x1f``) and escape
(``\\x1b``) characters, runs of both, null bytes, and broad unicode, so the
order-critical escape-then-separator replacement and the inverse
unescape-state-machine in :func:`parse_canonical` are stressed exactly
where a naive ``join``/``split`` would collide.

Proven here:
- **round-trip**: ``parse_canonical(canonical_subject_id(x)) == x`` for any
  :class:`~effaced.CompositeSubjectId` ``x`` of adversarial elements, and a
  bare ``str`` is byte-identical through ``canonical_subject_id``;
- **injectivity**: distinct composite keys never share a canonical string,
  including arity-shifted keys whose elements differ only in where a
  separator-like character falls.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from schema_strategies import scaled_examples

from effaced import CompositeSubjectId, canonical_subject_id, parse_canonical

pytestmark = pytest.mark.property

_SEPARATOR = "\x1f"
"""The reserved element separator — mirrored from the module under test so a
silent change to it makes this fuzz suite fail loudly rather than drift."""

_ESCAPE = "\x1b"
"""The reserved escape character — mirrored for the same reason."""

# Characters most likely to break a hand-rolled escaper: the two reserved
# control chars themselves, a null byte, and ordinary text that surrounds
# them in a realistic composite key value.
_ADVERSARIAL_ALPHABET = _SEPARATOR + _ESCAPE + "\x00" + "ab:" + "\U0001f600"

# Elements skewed hard toward the reserved characters: short strings drawn
# almost entirely from the escape/separator alphabet surface the ambiguous
# boundaries (runs of escapes, a separator adjacent to an escape) that a
# correct escaper must disambiguate and a broken one cannot.
_reserved_heavy = st.text(alphabet=_ADVERSARIAL_ALPHABET, min_size=1, max_size=8)

# Broad-unicode elements (still non-empty, as the model requires) so the
# round-trip is not over-fit to the reserved characters alone.
_broad_unicode = st.text(min_size=1, max_size=8)

# A single non-empty element: mostly reserved-heavy, sometimes broad unicode,
# occasionally a literal that is *only* reserved characters.
_adversarial_element = st.one_of(
    _reserved_heavy,
    _broad_unicode,
    st.sampled_from([_SEPARATOR, _ESCAPE, _ESCAPE + _SEPARATOR, _SEPARATOR + _ESCAPE]),
)


@st.composite
def _adversarial_keys(draw: st.DrawFn) -> CompositeSubjectId:
    """Draw a :class:`CompositeSubjectId` of one-to-five hostile elements."""
    elements = draw(st.lists(_adversarial_element, min_size=1, max_size=5))
    return CompositeSubjectId(values=tuple(elements))


@st.composite
def _collision_prone_pairs(
    draw: st.DrawFn,
) -> tuple[CompositeSubjectId, CompositeSubjectId]:
    """Draw two keys that share characters across a shifted element boundary.

    Independently drawn keys collide only by astronomical chance even under a
    *broken* serializer, so a naive injectivity test is nearly vacuous. This
    instead splits one flat character run at two different boundaries — the
    regime where a missing escape actually collides (``("a\\x1f", "b")`` vs
    ``("a", "\\x1fb")``) — so the assertion has real bugs to catch.
    """
    segments = draw(st.lists(_adversarial_element, min_size=2, max_size=5))
    cut_left = draw(st.integers(min_value=1, max_value=len(segments) - 1))
    cut_right = draw(st.integers(min_value=1, max_value=len(segments) - 1))
    left = CompositeSubjectId(values=("".join(segments[:cut_left]), "".join(segments[cut_left:])))
    right = CompositeSubjectId(
        values=("".join(segments[:cut_right]), "".join(segments[cut_right:]))
    )
    return left, right


@given(key=_adversarial_keys())
@settings(max_examples=scaled_examples(1), deadline=None)
def test_canonical_round_trips_under_adversarial_elements(key: CompositeSubjectId) -> None:
    """``parse_canonical(canonical_subject_id(x)) == x`` for any hostile key.

    If a value's separator or escape characters could forge an element
    boundary, the parsed key would gain, lose, or reshuffle an element and
    this equality would break.
    """
    serialized = canonical_subject_id(key)
    assert parse_canonical(serialized) == key


@given(pair=_collision_prone_pairs())
@settings(max_examples=scaled_examples(1), deadline=None)
def test_distinct_keys_never_collide(
    pair: tuple[CompositeSubjectId, CompositeSubjectId],
) -> None:
    """Distinct composite keys serialize to distinct canonical strings.

    A collision here is a cross-subject bleed: the audit trail, outbox, and
    SQL would treat two different subjects as one. Equal canonical strings
    are allowed *only* when the keys are themselves equal. The pair shares a
    flat character run split at two boundaries — the regime where a missing
    escape collides — so this has real bugs to catch, not just chance-distinct
    random keys.
    """
    left, right = pair
    same_string = canonical_subject_id(left) == canonical_subject_id(right)
    assert same_string == (left == right)


@given(
    head=_adversarial_element,
    middle=_adversarial_element,
    tail=_adversarial_element,
    pivot=st.sampled_from([_SEPARATOR, _ESCAPE, ":", _ESCAPE + _SEPARATOR]),
)
@settings(max_examples=scaled_examples(1), deadline=None)
def test_boundary_ambiguity_is_defeated(head: str, middle: str, tail: str, pivot: str) -> None:
    """The ``("a", "b:c")`` vs ``("a:b", "c")`` ambiguity never collides.

    Splitting the same characters at two different element boundaries — the
    exact ambiguity the docstring promises to defeat — must yield distinct
    canonical strings. ``pivot`` is the character (separator-like or the
    escape itself) that a naive serializer would let leak across the
    boundary; the two arrangements share every character yet must not share
    a canonical form.
    """
    left = CompositeSubjectId(values=(head, middle + pivot + tail))
    right = CompositeSubjectId(values=(head + pivot + middle, tail))
    if left == right:  # same characters can land the same key — then equality is correct
        assert canonical_subject_id(left) == canonical_subject_id(right)
    else:
        assert canonical_subject_id(left) != canonical_subject_id(right)


@given(value=st.text(min_size=1, max_size=16))
@settings(max_examples=scaled_examples(1), deadline=None)
def test_bare_str_is_its_own_canonical_form(value: str) -> None:
    """A single-column subject's bare ``str`` is byte-identical through canon.

    Single-column schemas must stay byte-for-byte the same in storage and
    the audit trail as before composite keys existed (ADR 0025), even when
    the string itself contains the reserved separator or escape characters.
    """
    assert canonical_subject_id(value) == value
