"""Claim-extraction precision: negation, code fences, quoted mentions, lists.

Focused regression tests for the stress_hard findings -- a misparse here is a
phantom verdict on something never claimed (or a missed real claim).
"""

from __future__ import annotations

from redpen.claims import extract_claims
from redpen.patterns import created_paths, strip_quoted


def _probes(text, source="transcript"):
    return {s.name for c in extract_claims(text, source) for s in c.probe_specs}


def _paths(text):
    return [s.kwargs["path"] for c in extract_claims(text)
            for s in c.probe_specs if s.name == "file_present"]


# --- negation-aware (a stated NON-completion is not a claim) -----------------


def test_negated_completion_is_not_a_claim():
    assert extract_claims("Not done yet — the tree is still uncommitted and I haven't pushed.") == []


def test_positive_done_still_runs_the_default_suite():
    names = _probes("All done, everything is committed.")
    assert "git_clean" in names and "contradiction_scan" in names


# --- code fences (example code is not a claim) -------------------------------


def test_trigger_inside_a_code_fence_is_not_a_claim():
    text = "Here's the helper I added:\n```python\ndef run():\n    # tests pass here\n    return True\n```"
    assert "tests_pass" not in _probes(text)


def test_git_command_in_a_code_fence_is_not_a_push_claim():
    text = "Here is the deploy recipe for later:\n```sh\ngit push origin main\n```"
    assert "git_pushed" not in _probes(text)


# --- quoted / sarcastic mentions ---------------------------------------------


def test_quoted_action_phrase_is_not_a_claim():
    assert extract_claims("Yeah, because 'just push it' is ever that simple. Anyway, take a look.") == []


def test_quoted_path_still_extracts_the_creation():
    assert _paths("I created 'src/app.py' for the feature.") == ["src/app.py"]


def test_contraction_apostrophes_do_not_swallow_a_real_trigger():
    # 'I've' / 'it's' must not be read as a quote span that eats the verb.
    assert strip_quoted("I've pushed it and it's all committed.") == "I've pushed it and it's all committed."
    names = _probes("I've pushed it and it's all committed.")
    assert "git_pushed" in names and "git_clean" in names


# --- conjunctions ("Created X and Y") ----------------------------------------


def test_created_x_and_y_extracts_both_paths():
    assert created_paths("Created src/x.py and src/y.py for the feature.") == ["src/x.py", "src/y.py"]
    assert _paths("Created src/x.py and src/y.py for the feature.") == ["src/x.py", "src/y.py"]


def test_created_comma_list_extracts_all_paths():
    assert created_paths("Created a/one.py, b/two.py, and c/three.py.") == [
        "a/one.py", "b/two.py", "c/three.py",
    ]


def test_markdown_table_row_is_not_a_claim():
    # A status table row is structured data, not an "I did X" assertion.
    assert extract_claims("| file | status |\n| z.py | created |\n| q.py | deleted |") == []


def test_prose_with_a_pipe_is_still_a_claim():
    # A normal sentence that merely contains a pipe is not a table row.
    names = _probes("I created src/app.py | the new entrypoint")
    assert "file_present" in names
