"""Baseline of cases where RedPen currently diverges from ground truth.

These are the genuine findings this suite surfaces -- NOT bugs in the suite. The
pytest layer treats them as expected (a known-finding case must still diverge;
if one starts passing, update this file). The report recomputes everything from
scratch and does not depend on this list.

Each entry: case id -> (category, root-cause tag). Categories are the three the
report headlines: false_FAIL, false_OK, misparse.
"""

from __future__ import annotations

KNOWN_DIVERGENCES = {
    # --- false FAIL: the contradiction engine cries wolf -------------------
    "contradiction/benign_grep_nomatch": ("false_FAIL", "benign-nonzero-exit"),
    "contradiction/benign_diff": ("false_FAIL", "benign-nonzero-exit"),
    "contradiction/benign_test_builtin": ("false_FAIL", "benign-nonzero-exit"),
    "contradiction/benign_tool_exit3": ("false_FAIL", "benign-nonzero-exit"),
    "contradiction/sig_in_cat_traceback": ("false_FAIL", "signature-in-benign-output"),
    "contradiction/sig_in_echo_failed": ("false_FAIL", "signature-in-benign-output"),
    "contradiction/build_sig_in_cat": ("false_FAIL", "signature-in-benign-output"),
    "contradiction/zero_failed_summary": ("false_FAIL", "zero-failed-regex"),
    "contradiction/fail_then_pass_rerun": ("false_FAIL", "not-last-run-aware"),
    "contradiction/fail_then_pass_build": ("false_FAIL", "not-last-run-aware"),
    # --- false FAIL: attribution / narration ------------------------------
    "attribution/toctou_git_clean_baseline_0": ("false_FAIL", "git_clean-no-attribution"),
    "attribution/toctou_git_clean_baseline_1": ("false_FAIL", "git_clean-no-attribution"),
    "claim_extraction/negation_not_done_dirty": ("false_FAIL", "negation-blind-narration"),
    # --- false OK ---------------------------------------------------------
    "attribution/other_session_edit": ("false_OK", "cross-session-git-attribution"),
    # --- misparse ---------------------------------------------------------
    "claim_extraction/sarcasm_not_done": ("misparse", "extracts-from-quote"),
    "claim_extraction/code_block_comment": ("misparse", "extracts-from-code-fence"),
    "claim_extraction/code_block_push": ("misparse", "extracts-from-code-fence"),
    "claim_extraction/created_x_and_y": ("misparse", "conjunction-misses-second-path"),
    "claim_extraction/split_chitchat_final": ("misparse", "final-message-only-scoping"),
}
