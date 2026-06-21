"""Dimension B -- claim misparse at extreme scale / noise.

Transcripts at the ceiling: hundreds of thousands of tokens, the claim buried in
volume, heavy unicode / zero-width / RTL / emoji, BOM / CRLF encodings, claims
adjacent to near-identical non-claims, code/JSON/diff blocks, sub-agent
interleaving, pathological whitespace. We assert the real claim is extracted and
the noise is NOT -- and that ambiguous embeddings degrade to no finding (a safe
miss), never a phantom verdict.

The property-based companion (test_property_misparse.py) fuzzes arbitrary noise
around a fixed claim and asserts the claim's verdict is invariant.
"""

from __future__ import annotations

from uharness.builders import (NBSP, RLO, TB, ZWSP, huge_noise, make_repo,
                               write_file, write_jsonl_bytes)
from uharness.model import OK, Built, ef

from ._helpers import case

AXIS = "B"

# Near-identical NON-claims that must never be mistaken for the real claim.
_DECOYS = [
    "I did not create {p} yet.",
    "Earlier I thought about creating {p} but decided against it.",
    "The plan mentions {p} (not done).",
    "I have not created {p} yet.",
    "```python\n# created {p} here -- example only\nopen('{p}')\n```",
    "```diff\n+++ {p}\n+def f(): ...\n```",
    "| file | status |\n| {p} | pending |",
    '{{"created": "{p}", "status": "planned"}}',
]


def _build(ws, *, path, final, noise_turns=0, bom=False, crlf=False,
           sidechain_noise=0, files=None, write_path=True, rng=None):
    root = make_repo(ws / "repo", files or {"README.md": "# x\n"})
    if path and write_path:
        write_file(root, path, "def f():\n    return 1\n")
    t = TB(cwd=root)
    t.user("do the work")
    if noise_turns and rng is not None:
        huge_noise(t, rng, turns=noise_turns)
    for _ in range(sidechain_noise):
        if rng is not None:
            t.assistant(rng.sentence(), sidechain=True)
            t.bash("ls", output="a b c", sidechain=True)
    if path and write_path:
        t.write(path)
    t.assistant(final)
    if bom or crlf:
        tp = write_jsonl_bytes(t, ws / "t.jsonl", bom=bom, crlf=crlf)
    else:
        tp = t.write_to(ws / "t.jsonl")
    return Built(cwd=root, transcript=tp)


def cases():
    out = []

    # 1) The claim buried under huge non-final noise (token ceiling). The final
    #    message holds the single real claim; everything before is ignored.
    for turns in (150, 300, 500, 700):
        def b(ws, rng, _t=turns):
            return _build(ws, path="src/app.py", final="Created src/app.py with the entrypoint.",
                          noise_turns=_t, rng=rng)
        out.append(case(f"{AXIS}/huge/{turns}turns", AXIS,
                        f"single claim under ~{turns} noisy turns",
                        b, [ef("file_present", true=True, accept={OK}, subject="src/app.py")],
                        tags=("misparse", "latency")))

    # 2) The real claim adjacent to near-identical non-claims, decoys, and
    #    code/JSON/diff/table blocks -- only the real claim is extracted.
    for i, decoy in enumerate(_DECOYS):
        for j, real in enumerate(["src/widget.py", "core/engine.py", "pkg/handler.py"]):
            def b(ws, rng, _decoy=decoy, _real=real):
                final = (_decoy.format(p="src/decoy.py") + "\n"
                         + f"Created {_real} with the implementation.\n"
                         + _decoy.format(p="src/other.py"))
                return _build(ws, path=_real, final=final, rng=rng)
            out.append(case(f"{AXIS}/decoy/{i:02d}_{j}", AXIS, f"real claim beside decoy #{i}",
                            b, [ef("file_present", true=True, accept={OK}, subject=real)],
                            tags=("misparse",)))

    # 2b) ALL decoys plus the one real claim in a single dense final message.
    for j, real in enumerate(["src/dense.py", "pkg/a.py", "deep/nested/m.py"]):
        def b(ws, rng, _real=real):
            blob = "\n".join(d.format(p=f"src/d{k}.py") for k, d in enumerate(_DECOYS))
            final = blob + f"\nCreated {_real} with the implementation.\n" + blob
            return _build(ws, path=_real, final=final, rng=rng)
        out.append(case(f"{AXIS}/decoy_dense/{j}", AXIS, "all decoys + one real claim in one message",
                        b, [ef("file_present", true=True, accept={OK}, subject=real)],
                        tags=("misparse",)))

    # 3) Unicode / wide / combining filenames -- extracted and resolved.
    # (Emoji-in-filename is intentionally excluded: a So-class codepoint isn't a
    # path word char, so the path regex can't parse it -- the safe outcome is a
    # MISS, which we don't assert as a confident OK.)
    for i, fn in enumerate(["café.py", "naïve.py", "日本語.py", "über_helper.py", "Ωmega.py",
                            "Москва.py", "αβγ.py", "résumé.py", "naïveté.py", "Straße.py"]):
        def b(ws, rng, _fn=fn):
            return _build(ws, path=_fn, final=f"Created {_fn} with the helper.", rng=rng)
        out.append(case(f"{AXIS}/unicode_path/{i:02d}", AXIS, f"unicode filename {fn!r}",
                        b, [ef("file_present", true=True, accept={OK}, subject=fn)],
                        tags=("misparse", "unicode")))

    # 4) Invisible / bidi / nbsp characters injected into the SURROUNDING text
    #    (not the path): they must not spawn a phantom; the real claim still reads.
    _INVIS = [("zwsp", ZWSP), ("rlo", RLO), ("nbsp", NBSP), ("emoji", "🎉🚀✨"),
              ("zwsp_run", ZWSP * 50), ("zwj", "👨‍👩‍👧"), ("combining", "é" + "́"),
              ("bidi_mix", RLO + "abc" + "‬"), ("nbsp_run", NBSP * 20),
              ("emoji_flag", "🇯🇵🇺🇸")]
    for name, ch in _INVIS:
        def b(ws, rng, _ch=ch):
            final = (f"{_ch} Some chatter {_ch} about the work {_ch}.\n"
                     f"Created src/mod.py for the feature.\n"
                     f"{_ch} Thanks! {_ch}")
            return _build(ws, path="src/mod.py", final=final, rng=rng)
        out.append(case(f"{AXIS}/invisible/{name}", AXIS, f"invisible/bidi noise around the claim ({name})",
                        b, [ef("file_present", true=True, accept={OK}, subject="src/mod.py")],
                        tags=("misparse", "unicode")))

    # 5) Encoding edges at the byte level: BOM, CRLF, BOM+CRLF.
    for name, bom, crlf in [("bom", True, False), ("crlf", False, True), ("bom_crlf", True, True)]:
        def b(ws, rng, _bom=bom, _crlf=crlf):
            return _build(ws, path="src/enc.py", final="Created src/enc.py with the loader.",
                          bom=_bom, crlf=_crlf, rng=rng)
        out.append(case(f"{AXIS}/encoding/{name}", AXIS, f"transcript with {name} bytes",
                        b, [ef("file_present", true=True, accept={OK}, subject="src/enc.py")],
                        tags=("misparse", "encoding")))

    # 6) Sub-agent interleaving: the parent's final claim is what gets graded.
    for n in (2, 8, 20, 40, 80):
        def b(ws, rng, _n=n):
            return _build(ws, path="src/parent.py",
                          final="Created src/parent.py via the sub-agents.",
                          sidechain_noise=_n, rng=rng)
        out.append(case(f"{AXIS}/subagent/{n}", AXIS, f"{n} interleaved sub-agent turns",
                        b, [ef("file_present", true=True, accept={OK}, subject="src/parent.py")],
                        tags=("misparse",)))

    # 7) Pathological whitespace around the claim.
    for i, pad in enumerate(["\t\t\t", "   " * 20, "\n\n\n", "     "]):
        def b(ws, rng, _pad=pad):
            final = f"{_pad}Created src/space.py{_pad}with the code.{_pad}"
            return _build(ws, path="src/space.py", final=final, rng=rng)
        out.append(case(f"{AXIS}/whitespace/{i:02d}", AXIS, f"pathological whitespace #{i}",
                        b, [ef("file_present", true=True, accept={OK}, subject="src/space.py")],
                        tags=("misparse",)))

    # 8) Pure noise / emoji-only finals -> nothing extracted (no phantom).
    for i, final in enumerate(["🎉🚀✨👍", "🙌", "...", "   ", "👀 lgtm", "🔥🔥🔥",
                               "🆗", "¯\\_(ツ)_/¯", "👍👍"]):
        def b(ws, rng, _final=final):
            return _build(ws, path=None, final=_final, write_path=False, rng=rng)
        out.append(case(f"{AXIS}/pure_noise/{i:02d}", AXIS, f"emoji/noise-only final {final!r}",
                        b, [], tags=("misparse",)))

    # 9) The claim is one sentence among many filler sentences in the final msg.
    #    Filler is deliberately trigger-word-free prose (the final message IS
    #    scanned, unlike the non-final noise turns).
    _FILLER = "The lake was calm and the morning light lay across the quiet hills. "
    for i, n_filler in enumerate([10, 25, 50, 80]):
        def b(ws, rng, _n=n_filler):
            filler = _FILLER * _n
            final = filler + " Created src/needle.py with the helper. " + filler
            return _build(ws, path="src/needle.py", final=final, rng=rng)
        out.append(case(f"{AXIS}/needle/{i:02d}", AXIS, f"claim among {n_filler} filler sentences",
                        b, [ef("file_present", true=True, accept={OK}, subject="src/needle.py")],
                        tags=("misparse",)))

    # 10) Claim surrounded by dense code / JSON / diff / table blocks (stripped or
    #     not matched), so only the prose claim is extracted.
    _BLOCKS = [
        "```json\n{{\"created\": [\"a.py\", \"b.py\"], \"pushed\": true}}\n```",
        "```diff\n--- a/old.py\n+++ b/new.py\n+def f(): ...\n-def g(): ...\n```",
        "| File | Action |\n| --- | --- |\n| z.py | created |\n| q.py | deleted |",
        "```sh\ngit push origin main && npm run build && pytest -q\n```",
        "```python\nopen('ghost.py', 'w')  # creates ghost.py\nassert tests_pass\n```",
    ]
    for i, blk in enumerate(_BLOCKS):
        for j, real in enumerate(["src/clean.py", "mod/pure.py"]):
            def b(ws, rng, _blk=blk, _real=real):
                final = _blk + f"\nCreated {_real} with the real implementation.\n" + _blk
                return _build(ws, path=_real, final=final, rng=rng)
            out.append(case(f"{AXIS}/blocks/{i:02d}_{j}", AXIS, f"claim beside code/JSON/diff/table block #{i}",
                            b, [ef("file_present", true=True, accept={OK}, subject=real)],
                            tags=("misparse",)))

    return out
