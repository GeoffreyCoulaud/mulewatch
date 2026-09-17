# Handoff: Zensical documentation site, phase 1

Date: 2026-09-17
Branch: `docs/zensical-site`, merged to `main` locally (no PR, operator decision)
Spec: `agents/specs/2026-09-17-zensical-docs-site.md` · Plan: `agents/plans/2026-09-17-zensical-docs-site-phase1.md`

## Current state

The operator documentation is a Zensical site of 15 French pages, built from `docs/` and published
to GitHub Pages at https://geoffreycoulaud.github.io/mulewatch/. Phase 1 was **reorganization
only**: files moved and split, no sentence rewritten. Phase 2 (the rewrite, with the frozen cut list
in spec §8) has not started.

The repository now separates the two natures of documentation:

- `docs/` is the Zensical root and is **published in full**. Anything not meant to be public does
  not belong there.
- `agents/` holds the work trace: `specs/`, `plans/`, `handoffs/`, `reference/`. Never published.

## What was built

- `docs/` flattened into 15 pages (`install`, `operate`, `vpn`, `high-id`, `settings`,
  `migration-1x`, `verify-image`, `troubleshooting-start`, `troubleshooting`, `limits`, `legal`,
  `glossary`, `index`, `contributing/architecture`, `contributing/testing`). File names are English,
  page titles French. `docs/runbooks/` is gone.
- `zensical.toml` at the repository root, `zensical` pinned as a dev dependency (0.0.62).
- `poe docs-build` (`zensical build --clean --strict`) and `poe docs-serve`.
- `.github/workflows/docs.yml`: builds on a push to `main` touching `docs/**`, `zensical.toml` or
  the workflow itself, then deploys to Pages. Actions pinned by SHA, like every other workflow here.
- `AGENTS.md`: orientation paths now point at `agents/`; the language rule states that everything
  under `docs/` is French; a new invariant records that `docs/` is published in full.
- Root `README.md` rewritten: French, one paragraph, a link to the site.

## Learned pitfalls

- **Zensical's default `toc` slugify strips accents.** "nœud" became `nud`, "métriques" became
  `metriques`, and 24 of 27 internal links broke on the first build. Fixed in `zensical.toml` with
  `toc.slugify = { object = "pymdownx.slugs.slugify", kwds = { case = "lower" } }`, which preserves
  them **and** matches the anchors GitHub generates, so the same link works on the site and in the
  repository. Do not revert this to the default.
- **`zensical build` exits 0 even when it reports broken links and dead anchors.** It prints them
  with file, line and column, then succeeds. `--strict` is what turns them into a failure, and it is
  why `poe docs-build` carries the flag. Without it, dead links publish silently.
- **Zensical builds every `.md` under `docs_dir`**, nav or no nav. There is no exclusion list. That
  is the whole reason `agents/` exists as a sibling directory rather than a subdirectory of `docs/`.
- **A single build with `docs/fr/` and `docs/en/` mixes the two languages** in one sidebar and one
  search index. Bilingual needs two configs and two builds (verified, spec §3).
- **Links from a page in `docs/contributing/` resolve relative to that subdirectory.** `install.md`
  there means `docs/contributing/install.md`. Needs `../install.md`.

## Not validated against real hardware

- **Nothing in this work touches a node, an image, or a runtime path.** The site is static and
  built from Markdown. There is no hardware claim to validate.
- **The Pages deployment itself** had not run at the time this handoff was written: Pages is enabled
  and the workflow fires on the merge to `main`. If `deploy-pages` fails, the usual cause is Pages
  not being in "GitHub Actions" build mode.
- **`test_connect_refused_raises_connect_error` fails on macOS**, unrelated to this branch: a socket
  bound without `listen` returns RST on Linux and silently drops SYNs on macOS, so the connection
  times out instead of being refused. The test documents the Linux assumption in its own comment. CI
  runs on `ubuntu-latest`, where it passes. Not introduced here, not fixed here.

## Suggested next step

**Phase 2: the rewrite.** The cut list is frozen in spec §8 and needs no further approval: six
duplicate sections to merge, three extractions, seven outright deletions, plus the style rules
(§8.5: no em dashes, one bold per paragraph, no nested block quotes). Also in phase 2: translating
`docs/contributing/architecture.md` and `docs/contributing/testing.md`, which currently sit in the
site in English (about 4 900 words).

Any deletion beyond the §8 list goes back to the operator before it happens.
