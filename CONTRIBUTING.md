# Contributing to spawnwp

Thanks for your interest in improving spawnwp! Contributions of all kinds are
welcome — bug reports, documentation fixes, and code.

## Ways to contribute

- **Report a bug** — open an issue with your OS/arch, what you ran, and the output.
  Never paste real secrets (passwords, knock sequences) into an issue.
- **Suggest a feature** — open an issue describing the use case.
- **Improve the docs** — the docs are plain Markdown under `docs/` (see below).
- **Send a pull request** — fork, branch, commit, open a PR against `main`.

## Working on the documentation

The docs site is built with [MkDocs](https://www.mkdocs.org/) and the
[Material](https://squidfunk.github.io/mkdocs-material/) theme. To preview locally:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install mkdocs-material
mkdocs serve        # live preview at http://127.0.0.1:8000
mkdocs build --strict   # fail on broken links / warnings
```

Content lives in `docs/*.md`; navigation and theme are configured in `mkdocs.yml`.
Keep pages as clean Markdown so the content stays portable.

## Pull request guidelines

- Keep changes focused; one logical change per PR.
- Match the existing style; shell scripts should pass `shellcheck` and `bash -n`.
- Update the docs and `docs/changelog.md` when behavior changes.
- Do not commit secrets, real domains, or generated `.env` / credentials files.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to uphold it.
