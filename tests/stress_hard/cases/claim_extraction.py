"""Claim-extraction adversarial cases -- the invisible single point of failure.

Each case crafts a final assistant message and asserts exactly which probes
RedPen should (and should not) extract. Extraction errors are tallied as
*misparse*: a phantom finding for a claim that was never made, or a miss of a
claim that was. Verdicts use loose accept sets (reality-driven) so these cases
isolate extraction, not verdict, behaviour -- except where a phrasing is meant
to trip a false verdict.

Ground truth is derived from a close reading of redpen/patterns.py and
redpen/claims.py (precision-first regexes; non-claims and path listings filtered;
the catch-all maps an accomplishment verb with nothing checkable to a labelled
UNVERIFIABLE 'unmapped' line; only the FINAL assistant message is read).
"""

from __future__ import annotations

from harness.builders import TB, make_repo, write_file
from harness.model import FAIL, OK, UNV, Built, Case, ef

from ._helpers import basic_case

AXIS = "claim_extraction"


def _suite(*, git_clean_true=True, pushed=None, touched=False):
    """The five default-suite EFs for a clean, no-upstream, no-touched-file repo."""
    return [
        ef("git_pushed", true=pushed),
        ef("git_clean", true=git_clean_true),
        ef("tests_pass", true=None),
        ef("todos_remaining", true=(True if touched else None), accept=({OK, UNV} if touched else {UNV})),
        ef("contradiction_scan", true=True, accept={OK}),
    ]


def cases():
    out = []
    c = out.append

    # ------------------------------------------------------------------ hedged
    # Hedged phrasing must NOT hide a concrete, checkable claim.
    c(basic_case(f"{AXIS}/hedge_pushed", AXIS, "hedge: 'I think I pushed it'",
                 final="I think I went ahead and pushed it to origin.",
                 efs=[ef("git_pushed", true=None)]))  # no upstream -> UNV
    c(basic_case(f"{AXIS}/hedge_committed", AXIS, "hedge: 'pretty sure it's committed'",
                 final="Pretty sure that's all committed now.",
                 efs=[ef("git_clean", true=True)]))
    c(basic_case(f"{AXIS}/hedge_tests_believe", AXIS, "hedge: 'I believe the tests are passing'",
                 final="I believe the tests are passing at this point.",
                 efs=[ef("tests_pass", true=None)]))  # nothing ran -> UNV
    c(basic_case(f"{AXIS}/hedge_wired_up", AXIS, "hedge: 'went ahead and wired it up'",
                 final="Went ahead and wired it up end to end.",
                 efs=[ef("unmapped", true=None)]))  # 'wired' -> catch-all unmapped

    # Vague hedges with NO checkable trigger and NO success-narration word must
    # extract NOTHING (no phantom, no invented default-suite).
    for i, phrase in enumerate([
        "That ought to do it.",
        "Should be all good now.",
        "I think we're in a much better spot.",
        "Take a look whenever you get a chance.",
        "Hopefully that covers what you needed.",
        "Have a look and tell me what you think.",
    ]):
        c(basic_case(f"{AXIS}/vague_nothing_{i:02d}", AXIS, f"vague, no claim: {phrase!r}",
                     final=phrase, efs=[]))

    # A bare success-narration word ('green') with nothing concrete pulls in the
    # whole default suite (by design) -- not a misparse, and crucially no FAIL.
    c(basic_case(f"{AXIS}/narration_green_suite", AXIS, "'should all be green' -> default suite, no FAIL",
                 final="Should all be green now.", efs=_suite()))

    # ------------------------------------------------------------- retractions
    # Earlier success claim retracted in the final message -> nothing to grade.
    c(basic_case(f"{AXIS}/retracted_not_done", AXIS, "retraction with no success word -> nothing",
                 final="Actually that broke — I'm still in the middle of it.",
                 pre_assistant=["All tests pass and I pushed everything. Done."],
                 efs=[]))
    c(basic_case(f"{AXIS}/retracted_partial", AXIS, "retraction then nothing concrete",
                 final="Hmm, scratch that, the previous attempt didn't work.",
                 pre_assistant=["Created app.py and committed it."],
                 efs=[]))
    # Retraction THEN redo -> the final (re-done) state is what gets graded.
    c(basic_case(f"{AXIS}/retracted_then_redone", AXIS, "retract then redo: grade final state",
                 final="The first push failed, but I retried and now it's committed for real.",
                 pre_assistant=["Pushed everything!"],
                 efs=[ef("git_pushed", true=None), ef("git_clean", true=True)]))

    # Negation-insensitivity finding: the agent explicitly says it is NOT done,
    # but the word 'done' trips success-narration -> the whole default suite runs
    # on a non-claim, and on a dirty tree git_clean FAILs a claim never made.
    def b_neg(ws, rng):
        root = make_repo(ws / "repo", {"app.py": "v1\n"})
        write_file(root, "app.py", "v1\nwork in progress\n")  # post-claim dirty tree
        t = TB(cwd=root)
        t.user("is it finished?")
        t.assistant("Not done yet — the tree is still uncommitted and I haven't pushed.")
        return Built(cwd=root, transcript=t.write_to(ws / "t.jsonl"))
    c(Case(f"{AXIS}/negation_not_done_dirty", AXIS,
           "'not done, uncommitted' must not be graded as a completion claim",
           b_neg, [], tags=("negation",)))

    # ------------------------------------------------ split across messages
    # Final message holds only a fragment -> only the fragment is graded.
    c(basic_case(f"{AXIS}/split_final_fragment", AXIS, "split claim: only final fragment graded",
                 final="...and then I pushed it up.",
                 pre_assistant=["I created src/app.py", "I also committed everything"],
                 efs=[ef("git_pushed", true=None)],
                 # the earlier file/commit claims live in non-final turns -> not seen
                 allow_phantom=()))
    # Final message is chit-chat; the real claims were earlier -> RedPen misses them.
    chit = basic_case(f"{AXIS}/split_chitchat_final", AXIS,
                      "real claims in earlier turn, final is chit-chat (final-msg scoping)",
                      final="Glad I could help! Let me know if you need anything else.",
                      pre_assistant=["I created src/app.py, ran the suite (all green), and pushed to origin."],
                      efs=[ef("git_pushed", true=None, note="claim made in a non-final turn")])
    chit.tags = ("scoping",)
    c(chit)

    # ------------------------------------------------ sarcasm / quoted failure
    # A failure quoted in PROSE (not tool output) must not become a verdict.
    c(basic_case(f"{AXIS}/quoted_old_failure", AXIS, "prose quotes an old failure, not a claim",
                 final="The old code used to throw 'tests failed' on startup; I fixed that.",
                 efs=[ef("unmapped", true=None)]))  # 'fixed' -> unmapped; quote is not scanned
    c(basic_case(f"{AXIS}/sarcasm_not_done", AXIS, "sarcastic non-claim",
                 final="Yeah, because 'just push it' is ever that simple. Anyway, take a look.",
                 efs=[]))
    c(basic_case(f"{AXIS}/describing_error_string", AXIS, "describing an error message string",
                 final="I added a guard that raises 'build failed' when the config is missing.",
                 efs=[ef("unmapped", true=None)]))  # 'added' guard (no path) -> unmapped

    # ------------------------------------------------------- code blocks
    # A trigger phrase inside a fenced code block is example code, not a claim.
    # RedPen reads the fence as prose, so the comment becomes a spurious
    # tests_pass finding (a misparse). The prose 'I added' is a real (generic)
    # claim -> unmapped.
    cb = basic_case(f"{AXIS}/code_block_comment", AXIS, "trigger words inside a code fence become a claim",
                    final="Here's the helper I added:\n```python\ndef run():\n    # tests pass here\n    return True\n```",
                    efs=[ef("unmapped", true=None)])
    cb.tags = ("codeblock",)
    c(cb)
    cb2 = basic_case(f"{AXIS}/code_block_push", AXIS, "git command in a code fence becomes a push claim",
                     final="Here is the deploy recipe for later:\n```sh\ngit push origin main\n```",
                     efs=[])
    cb2.tags = ("codeblock",)
    c(cb2)

    # ------------------------------------------------------- multilingual / emoji
    c(basic_case(f"{AXIS}/emoji_check_done", AXIS, "emoji ✅ triggers done-suite; mixed language",
                 final="✅ Listo. Tests passing now 🎉",
                 files={"README.md": "# x\n"},
                 efs=[
                     ef("tests_pass", true=None),
                     ef("git_pushed", true=None),
                     ef("git_clean", true=True),
                     ef("todos_remaining", true=None, accept={UNV}),
                     ef("contradiction_scan", true=True, accept={OK}),
                 ]))
    c(basic_case(f"{AXIS}/pure_emoji", AXIS, "pure emoji message, no claim",
                 final="🎉🚀✨👍", efs=[]))
    nlcase = basic_case(f"{AXIS}/non_english_done", AXIS, "non-English 'done' (English-only matcher)",
                        final="完成了，所有测试都通过了。", efs=[])
    nlcase.tags = ("multilingual",)
    c(nlcase)

    # ------------------------------------------------------------- weird paths
    c(basic_case(f"{AXIS}/path_unicode", AXIS, "unicode filename created",
                 final="Created café.py with the new helper.",
                 files={"café.py": "print('hi')\n"},
                 writes=["café.py"],
                 efs=[ef("file_present", true=True, subject="café.py")]))
    sp = basic_case(f"{AXIS}/path_with_space", AXIS, "filename with a space defeats the path regex",
                    final="Created my report.py in the root.",
                    files={"my report.py": "x\n"},
                    efs=[ef("unmapped", true=None, note="spaced path not matched by FILE_RE -> generic")])
    sp.tags = ("weirdpath",)
    c(sp)
    c(basic_case(f"{AXIS}/path_traversal_missing", AXIS, "path-traversal-looking claim to nonexistent file",
                 final="Created ../../../tmp/redpen_nope_zzz.py for the shared util.",
                 efs=[ef("file_present", true=False, accept={FAIL, UNV},
                         subject="..", note="traversal path; must not crash")]))

    # ----------------------------------------------------- precision / non-claims
    c(basic_case(f"{AXIS}/nonclaim_nothing_changed", AXIS, "'nothing to change' is not a claim",
                 final="Looked into it — nothing needed to change, the code was already correct.",
                 efs=[]))
    c(basic_case(f"{AXIS}/listing_not_claim", AXIS, "path listing is not a creation claim",
                 final="Relevant files:\nsrc/app.py — the entrypoint\nsrc/util.py — helpers",
                 efs=[]))
    c(basic_case(f"{AXIS}/no_type_errors_positive", AXIS, "'no type errors' must not match the non-claim regex",
                 final="mypy is clean — no type errors anywhere.",
                 files={"pyproject.toml": "[tool.mypy]\n"},
                 efs=[ef("typecheck_clean", true=None, note="mypy configured but not run this session -> UNV")]))
    c(basic_case(f"{AXIS}/double_negative", AXIS, "'didn't break anything' is not a success claim",
                 final="I didn't break anything and didn't need to touch the tests.",
                 efs=[]))

    # 'Created X and Y' -- FILE_RE attaches one path per verb, so Y is missed.
    conj = basic_case(f"{AXIS}/created_x_and_y", AXIS, "'Created X and Y' extracts only X (misses Y)",
                      final="Created src/x.py and src/y.py for the feature.",
                      files={"src/x.py": "a = 1\n", "src/y.py": "b = 2\n"},
                      writes=["src/x.py", "src/y.py"],
                      efs=[ef("file_present", true=True, subject="src/x.py"),
                           ef("file_present", true=True, subject="src/y.py",
                              note="second path after 'and' has no verb -> missed")])
    conj.tags = ("conjunction",)
    c(conj)

    return out
