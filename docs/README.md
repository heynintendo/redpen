# docs/ — RedPen assets

- `banner.png` — wide header image used at the top of the project README.
- `logo.png` — square pixel-art mascot (the examiner with a red pen).
- `logo.svg` — vector version of the logo.
- `mascot_ansi.txt` — truecolor ANSI half-block art printed as the header of
  `redpen check` on a color terminal. Loaded by `redpen/render.py` (and mirrored
  into the wheel as `redpen/_assets/mascot_ansi.txt` for installed copies). On a
  non-color/piped stdout, or with `--no-art`, RedPen falls back to a text title.
