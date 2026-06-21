# RedPen demo. Regenerate with:  bash docs/gen_demo.sh   (from the repo root)
# gen_demo.sh builds a clean, deterministic workspace ($REDPEN_DEMO_WORK) with two
# transcripts, then runs this tape. The money shot: an agent claims "done, all
# tests pass" over its own failing pytest output (RedPen catches it with the
# quoted evidence), then a clean pass on honest work.

Output docs/demo_raw.gif

Set Shell "bash"
Set FontSize 19
Set Width 1180
Set Height 820
Set Padding 22
Set Theme "Dracula"
Set TypingSpeed 26ms

# --- hidden setup: repo venv on PATH, truecolor so the pixel mascot renders ---
Hide
Type `export PATH=$PWD/.venv/bin:$PATH; export COLORTERM=truecolor TERM=xterm-256color; cd "$REDPEN_DEMO_WORK"; export PS1='$ '; clear`
Enter
Show

# --- 1) the money shot: agent claims success, RedPen catches the real FAIL ---
Type "redpen check --transcript caught.jsonl"
Sleep 500ms
Enter
Sleep 4s

# --- 2) a clean pass on honest work (compact, no mascot) ---------------------
Type "redpen check --transcript honest.jsonl --no-art"
Sleep 500ms
Enter
Sleep 4s
