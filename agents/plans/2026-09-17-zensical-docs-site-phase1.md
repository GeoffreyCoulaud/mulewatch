# Plan: phase 1, reorganize and publish

Spec: `agents/specs/2026-09-17-zensical-docs-site.md` (moves to `agents/specs/` in step 1).
Branch: `docs/zensical-site`.

**Scope boundary.** Files move, are split into the target pages, and every path reference is
fixed. Prose is carried over as is, apart from the two factual errors in spec §9. No sentence is
rewritten for style: that is phase 2.

Phase 2 needs no plan document of its own. Spec §8 already freezes what gets merged, extracted and
deleted; the phase 2 work is that list turned into one task per page, and it is written once phase 1
has landed and the real page boundaries are visible.

## Step 1: move the work trace to `agents/`

```bash
mkdir -p agents
git mv agents/specs agents/plans agents/handoffs agents/reference agents/
```

This plan file and the spec move with it (they are in `agents/plans/` and `agents/specs/` at the time of
the move).

## Step 2: fix every reference to a moved path

Mechanical, then verified by grep. Targets measured in discussion:

| File | Mentions |
|---|---|
| `AGENTS.md` | 10 |
| `README.md` | 5 (rewritten wholesale in step 6 anyway) |
| `SECURITY.md` | 1 |
| `deploy/.env.example` | 1 |
| `.github/workflows/validate.yml` | 1 (comment) |
| `packages/crawler/pyproject.toml` | 3 (pytest marker descriptions) |
| `packages/crawler/src/mulewatch/adapters/mule_ec/codes.py` | 1 (module docstring) |
| `packages/crawler/src/mulewatch/adapters/mule_ec/codec.py` | 1 (module docstring) |
| `packages/crawler/src/mulewatch/application/run_download_cycle.py` | 1 |
| `packages/crawler/src/mulewatch/merge/__init__.py` | 1 |
| `packages/crawler/src/mulewatch/adapters/persistence_sqlite/connection.py` | 1 |
| `packages/crawler/tests/adapters/mule_ec/test_codes.py` | 1 |
| `packages/crawler/tests/integration/conftest.py` | 2 |

Inside `agents/`, relative links between archives survive the move (they move together). Prose
mentions of the form `agents/specs/...`, `agents/handoffs/...`, `agents/reference/...`, `agents/plans/...`
are rewritten to `agents/...` across the whole repository.

Verification: `grep -rn 'docs/\(specs\|plans\|handoffs\|reference\)' . --exclude-dir=.git` returns
nothing.

## Step 3: split and rename the operator documentation

Per spec §4. Each target page is `git mv` plus a split where one source feeds several pages, so
history follows the content:

| Source | Target |
|---|---|
| `docs/README.md` | `docs/index.md` |
| `docs/runbooks/deployment.md` steps 1 to 7 | `docs/install.md` |
| `docs/runbooks/deployment.md` annex A | `docs/vpn.md` |
| `docs/runbooks/deployment.md` annexes C, D + `administration.md` § High-ID | `docs/high-id.md`, `docs/settings.md` |
| `docs/runbooks/deployment.md` annex E | `docs/migration-1x.md` |
| `docs/runbooks/deployment.md` glossary | `docs/glossary.md` |
| `docs/runbooks/administration.md` lifecycle and tuning | `docs/operate.md` |
| `docs/runbooks/administration.md` § image authenticity | `docs/verify-image.md` |
| `docs/runbooks/administration.md` § known limits | `docs/limits.md` |
| `docs/runbooks/troubleshooting.md` first-deployment cards | `docs/troubleshooting-start.md` |
| `docs/runbooks/troubleshooting.md` operator diagnostics | `docs/troubleshooting.md` |
| `docs/legal-and-privacy.md` | `docs/legal.md` |
| `docs/architecture.md` | `docs/contributing/architecture.md` |
| `docs/testing-guide.md` | `docs/contributing/testing.md` |

`docs/runbooks/` is then empty and disappears.

Duplicated sections listed in spec §8.1 are **not merged here**: both copies move to their target
page and the merge happens in phase 2. The exception is where a section splits across two target
pages (High-ID, ports and metrics): the split is the move.

Every intra-documentation link and anchor is updated to the new page names. Step 7's `--strict`
build is what proves none was missed.

## Step 4: fix the two documentation bugs (spec §9)

- `docs/operate.md`: `uv run python -m mulewatch.merge ...` and `uv run python -m mulewatch.compact ...`
  become `docker compose exec mulewatch python -m ...`.
- `docs/troubleshooting.md`: `uv run python -m mulewatch validate-config` likewise.

A reader who followed the install guide downloaded a ZIP: no repository clone, no `uv`. The `uv`
form stays only in `docs/contributing/`, where it is correct.

## Step 5: Zensical configuration

1. `uv add --dev zensical` (dev dependency only, never in the production image, same rule as
   `vex_guards`). Pin the exact version; 0.0.62 is current.
2. `zensical.toml` at the repository root, per spec §6: `site_name`, `site_url`, `docs_dir = "docs"`,
   `site_dir = "site"`, the explicit `nav` of spec §5, `[project.theme] language = "fr"`, and the
   navigation features (`navigation.sections`, `navigation.instant`, `navigation.tracking`,
   `navigation.path`).
3. Add `site/` to `.gitignore`.
4. Add a `poe` task `docs-serve` (local preview) and `docs-build` (`zensical build --clean --strict`).

`--strict` matters: a plain `zensical build` **reports** broken links and dead anchors with
file, line and column, then **exits 0**. Verified on 0.0.62. Without `--strict`, a push publishes
dead links silently.

## Step 6: publishing

1. `.github/workflows/docs.yml`, based on the workflow Zensical scaffolds: `configure-pages@v6`,
   `checkout@v7`, `setup-python@v6`, `pip install zensical` (version-pinned), `zensical build --clean
   --strict`, `upload-pages-artifact@v5` with `path: site`, `deploy-pages@v5`. Trigger: push to
   `main`, plus `workflow_dispatch`. Permissions `contents: read`, `pages: write`, `id-token: write`.
   No third-party action (spec §10).
2. Enable GitHub Pages on the repository in "GitHub Actions" mode.
3. Rewrite the root `README.md`: French, one paragraph, a link to
   `https://geoffreycoulaud.github.io/mulewatch/`, and the two commands a developer needs
   (`./scripts/setup-dev.sh`, `uv run poe check`).
4. Amend `AGENTS.md` per spec §11: orientation paths point at `agents/`; the language rule gains
   "everything under `docs/` is French, it is the published site"; a new invariant states that
   `docs/` is the Zensical root and is published in full.

## Step 7: verification

```bash
uv run poe check                      # the full gate, unchanged by this work
uv run poe docs-build                 # must print "No issues found"
grep -rn 'docs/\(specs\|plans\|handoffs\|reference\|runbooks\)' . --exclude-dir=.git --exclude-dir=site
```

Then a local preview, checking by hand that the five navigation groups are in reading order, that
the search returns French results, and that the occasional pages (migration, image verification) are
reachable but off the main path.

Phase 1 is done when the gate is green, the strict build is clean, and the site is live at the spec
§9 URL.

## What is deliberately not done in phase 1

- No sentence rewritten for style, no section deleted. The duplicates of spec §8.1 are still there,
  now sitting on separate pages.
- No translation of `docs/contributing/architecture.md` and `docs/contributing/testing.md`. They
  land in the site in English and are translated in phase 2. The site is briefly inconsistent, on
  two pages, in the contributor section. Accepted: the alternative is holding the whole
  reorganization hostage to 4 900 words of translation.
- No archive under `agents/` is rewritten, translated or reorganized.
