"""Dimension B, property-based -- the oracle-free half.

Instead of hand-writing the right answer for noisy inputs, we assert *properties*
that must hold for ALL of them. The robust framing: take noise that is itself
INERT (extracts to nothing on its own) and assert that framing the real claim
with it changes neither the claim nor introduces a phantom. If hypothesis finds
inert noise that drops the claim or invents a file claim, that's a misparse the
deterministic cases missed.

    REDPEN_STRESS_ULTRA=1 .venv/bin/python -m pytest tests/stress_ultra/test_property_misparse.py
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from redpen.claims import extract_claims

pytestmark = pytest.mark.slow

# Arbitrary text, minus surrogates (which aren't valid in a real transcript).
_noise = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=300)


def _probes(text):
    return [(s.name, s.kwargs.get("path")) for c in extract_claims(text) for s in c.probe_specs]


def _inert(*chunks):
    """Restrict to noise that is genuinely a non-claim on its own and carries no
    code fence (a fence legitimately swallows whatever it wraps)."""
    for c in chunks:
        assume("```" not in c)
        assume(_probes(c) == [])


@settings(max_examples=600, deadline=None)
@given(before=_noise, after=_noise)
def test_inert_noise_does_not_change_a_file_claim(before, after):
    _inert(before, after)
    probes = _probes(before + "\nCreated src/app.py.\n" + after)
    # the real claim survives ...
    assert ("file_present", "src/app.py") in probes, "inert noise dropped the real claim"
    # ... and no phantom file claim for any other path is invented.
    files = sorted(p for (n, p) in probes if n == "file_present")
    assert files == ["src/app.py"], f"inert noise invented a phantom file claim: {files}"


@settings(max_examples=400, deadline=None)
@given(before=_noise, after=_noise)
def test_inert_noise_never_invents_a_fail(before, after):
    _inert(before, after)
    # Around a benign true claim, inert noise must not add any second claim at all.
    probes = _probes(before + "\nCreated src/app.py.\n" + after)
    assert probes == [("file_present", "src/app.py")], f"inert noise added a phantom: {probes}"


@settings(max_examples=400, deadline=None)
@given(before=_noise, after=_noise)
def test_negated_completion_in_inert_noise_is_never_graded(before, after):
    _inert(before, after)
    probes = _probes(before + "\nI did not finish; nothing is committed and I have not pushed.\n" + after)
    assert probes == [], f"a negated non-claim was graded: {probes}"
