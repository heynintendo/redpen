# RedPen demo. Regenerate with:  bash docs/gen_demo.sh   (from the repo root)
# gen_demo.sh runs this tape, then trims the static `claude -p` wait so the GIF
# stays tight. Shows the money shot: a mixed deterministic check (✓/✗/⚠), then
# a one-call --deep full-request audit using the test fixture.

Output docs/demo_raw.gif

Set Shell "bash"
Set FontSize 19
Set Width 1180
Set Height 760
Set Padding 22
Set Theme "Dracula"
Set TypingSpeed 28ms

# --- hidden setup: venv `redpen` on PATH, fast model for the demo, clean prompt
Hide
Type "export PATH=$PWD/.venv/bin:$PATH; export REDPEN_LLM_MODEL=haiku; export PS1='$ '; clear"
Enter
Show

# --- 1) deterministic check on a mixed claim (some true, some false) ---------
Type "redpen check 'I created README.md. I created config.py. The tests pass.'"
Sleep 500ms
Enter
Sleep 2500ms

# --- 2) deep full-request audit using the fixture (one live LLM call) --------
Type "redpen check --deep --transcript tests/fixtures/sample_session.jsonl"
Sleep 500ms
Enter
Sleep 22s
