# Working on macro-platform

- Reply in Classical Arabic (فصحى); keep technical terms in English.
- Architecture mirrors `abduswaidan-glitch/gold-dashboard`: static `index.html` + `data.json` built by
  `fetch_macro.py` in GitHub Actions, served by GitHub Pages; Claude report via `ANTHROPIC_API_KEY`.
- Tests: `python -m pytest -q tests`. Local preview: `python fetch_macro.py --demo --out <dir>/data.json`
  then serve `index.html` next to it.
- Never publish broken data: new series go through `build.validate`; on failure the last good
  `data/raw/<ID>.csv` stays and a warning shows on the page.
- `data.json` and `data/` are written by the bot; do not hand-edit them. Demo data must never be committed.
- Page text is Arabic; every string from data or Claude goes through `esc()`, links through `safeUrl()`.
