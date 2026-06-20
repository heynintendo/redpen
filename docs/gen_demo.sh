#!/usr/bin/env bash
# Regenerate docs/demo.gif reproducibly.
#
# 1. vhs records docs/demo.tape live (the --deep step makes one real headless
#    `claude -p` call, so there's a ~15s static "thinking" stretch).
# 2. ffmpeg removes that dead-air window and re-times, keeping the verdicts and
#    the audit readable, so the published GIF stays tight (~17s).
#
# Requires: vhs, ffmpeg (brew install vhs ffmpeg). Run from the repo root.
set -euo pipefail
cd "$(dirname "$0")/.."

vhs docs/demo.tape   # -> docs/demo_raw.gif

# Drop seconds [9, 20] -- the static LLM wait between the command and the audit.
# Everything before (typing + verdicts) and after (the audit) is preserved.
# A regenerated palette keeps the trimmed GIF small (concat alone bloats it).
ffmpeg -y -i docs/demo_raw.gif -filter_complex \
  "[0:v]trim=0:9,setpts=PTS-STARTPTS[a];[0:v]trim=20,setpts=PTS-STARTPTS[b];\
   [a][b]concat=n=2:v=1,fps=18[c];[c]split[s0][s1];\
   [s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer[out]" \
  -map "[out]" docs/demo.gif

rm -f docs/demo_raw.gif
echo "wrote docs/demo.gif"
