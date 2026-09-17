# Spec: a navigable French documentation site on Zensical + GitHub Pages

Date: 2026-09-17
Status: proposed, awaiting operator approval

## 1. Problem

The operator-facing documentation is four dense Markdown files read on GitHub: three runbooks plus
the legality page, about 12 000 words of French prose. They work, but they are read by scrolling,
they duplicate each other in half a dozen places, and they carry a decision journal (dated records,
reversed decisions, cross-links into specs) that no operator needs. There is no published site.

Two goals, in this order:

1. **Publish** a navigable documentation site on GitHub Pages, built by Zensical.
2. **Rewrite** the published pages so they are simple to follow, jargon-free, concise and direct.

## 2. Decisions taken in discussion (2026-09-17)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Zensical**, not MkDocs | Operator decision on project governance. Not up for re-litigation. |
| D2 | **Everything under `docs/` is French**, contributor section included | A site that is French except two pages is a rule nobody can remember. Note: bilingual is technically possible today (see §3, verified); the reason to stay monolingual is the cost of keeping 12 000 words in sync forever, not a tool limitation. |
| D3 | **`agents/` holds the work trace**: `specs/`, `plans/`, `handoffs/`, `reference/` move there | Zensical builds every `.md` under `docs_dir` regardless of nav, so the boundary between published and unpublished must be a directory boundary. |
| D4 | **`docs/` is the Zensical root**, and everything in it is published | Authoritative docs, useful to humans and to AI agents alike. |
| D5 | `architecture.md` and `testing-guide.md` **stay in `docs/`**, in a contributor section, and get translated to French | They are maintainer docs, but they are authoritative. Per D2 they are translated. |
| D6 | The root `README.md` becomes **French and minimal**: one paragraph plus a link to the site | The site becomes the single entry point. |
| D7 | Two phases: **reorganize, then rewrite** | A working URL lands early; the rewrite is the long pole and proceeds page by page. |
| D8 | Cuts are **deletions and extractions**, not just rephrasing | Approved in discussion. The frozen list is §8. |
| D9 | Default GitHub Pages domain: `https://geoffreycoulaud.github.io/mulewatch/` | No custom domain. |

## 3. What Zensical can and cannot do about languages (verified 2026-09-17 on 0.0.62)

Verified empirically by building two throwaway sites, not by reading the documentation.

**Bilingual works today.** The shipped theme has `partials/alternate.html`, included by
`partials/header.html` whenever `config.extra.alternate` is set, and `base.html` emits the matching
`<link rel="alternate" hreflang>` tags. With two configuration files, one per language:

```toml
# zensical.toml (French, the default)      # zensical.en.toml (English)
docs_dir = "docs/fr"                       docs_dir  = "docs/en"
site_dir = "site"                          site_dir  = "site/en"
[project.theme] language = "fr"            [project.theme] language = "en"
[[project.extra.alternate]] ...            [[project.extra.alternate]] ...
```

`zensical build --clean` then `zensical build --config-file zensical.en.toml` produces one `site/`
tree carrying both languages. Each language gets: its own navigation (verified: the French pages
list only French entries), its own `search.json` with the right stemmer, the right `<html lang>`,
and a working language selector in the header. One artifact, one Pages deployment.

**What is genuinely missing**, and is what the backlog item covers: automatic fallback to the
default language for an untranslated page, navigation title translation, and `.fr.md` suffix file
naming. `mkdocs-static-i18n` remains an **open** backlog item
([zensical/backlog#1](https://github.com/zensical/backlog/issues/1), no ETA); the `nav_translations`
request ([zensical/zensical#35](https://github.com/zensical/zensical/issues/35)) and the gettext
proposal ([zensical/zensical#636](https://github.com/zensical/zensical/issues/636)) are folded into
it. A separate open item ([zensical/backlog#141](https://github.com/zensical/backlog/issues/141))
is about **documenting** ways to organize multilingual projects, which confirms the manual route is
intended, not accidental.

Also verified: a single build with `docs/fr/` and `docs/en/` under one `docs_dir` does **not** work
well. The sidebar then lists both languages at once and there is one mixed search index. The
two-build route is what avoids this.

Consequence: D2 is a content-maintenance decision, not a technical constraint. Should it ever be
reversed, the structural cost is one extra config file and one extra build step, and the layout in
§4 is already compatible (a `docs/fr/` prefix plus a sibling `docs/en/`).

## 4. Target repository layout

**File names are English, page titles are French.** English paths keep URLs ASCII-clean, keep the
repository consistent with the rest of the codebase, and survive a future `docs/fr` + `docs/en`
split untouched. What the reader sees is the `nav` title, which is French.

```
docs/                       Zensical root, 100 % published, 100 % French
  index.md                  Accueil                        (was docs/README.md)
  install.md                Installer un nœud              (was runbooks/deployment.md, steps 1 to 7)
  operate.md                Faire tourner un nœud          (was runbooks/administration.md)
  vpn.md                    Passer derrière un VPN         (was deployment.md annex A)
  high-id.md                Devenir High-ID                (merges annex C + administration § High-ID)
  settings.md               Régler le nœud                 (ports, metrics, reverse proxy, catalog-only)
  troubleshooting-start.md  Le déploiement bloque          (troubleshooting.md, first-deployment cards)
  troubleshooting.md        Diagnostics avancés            (troubleshooting.md, operator diagnostics)
  migration-1x.md           Migrer un nœud 1.x             (was deployment.md annex E)
  verify-image.md           Vérifier une image             (was administration.md § image authenticity)
  limits.md                 Limites connues                (was administration.md § known limits, cut)
  legal.md                  Légalité et vie privée         (was legal-and-privacy.md)
  glossary.md               Glossaire                      (was deployment.md § minimal glossary)
  contributing/
    architecture.md         Architecture du code           (translated)
    testing.md              Lancer les tests               (was testing-guide.md, translated)
agents/                     work trace, never published
  specs/ plans/ handoffs/ reference/
zensical.toml
.github/workflows/docs.yml
```

`docs/runbooks/` disappears as a directory: the runbook split (deploy / administer / troubleshoot)
was a filesystem convention that the site navigation now expresses better.

## 5. Site map

Five navigation groups, in reading order:

| Group | Pages |
|---|---|
| **Démarrer** | Accueil, Installer un nœud, Faire tourner un nœud |
| **Aller plus loin** | Passer derrière un VPN, Devenir High-ID, Régler le nœud |
| **Quand ça coince** | Le déploiement bloque, Diagnostics avancés |
| **Comprendre** | Comment ça marche, Légalité et vie privée, Glossaire |
| **Contribuer** | Architecture du code, Lancer les tests |

`Migrer un nœud 1.x` and `Vérifier une image` are occasional pages: linked from where they matter
(a banner at the top of "Installer", a line in "Faire tourner"), listed in the nav under "Aller plus
loin" but never on the main path. `Limites connues` sits at the end of "Comprendre".

## 6. Zensical configuration

`zensical.toml` at the repository root:

```toml
[project]
site_name = "mulewatch"
site_url = "https://geoffreycoulaud.github.io/mulewatch/"
docs_dir = "docs"
site_dir = "site"
nav = [ ... ]           # §5, explicit, no literate-nav plugin

[project.theme]
language = "fr"
```

Navigation features enabled: `navigation.sections` (top-level groups as sidebar groups),
`navigation.instant`, `navigation.tracking`, `navigation.path` (breadcrumbs). Search is built in and
takes its language from `theme.language`.

`site/` is added to `.gitignore`. Zensical is a **dev dependency only** (`uv add --dev zensical`):
it never ships in the production image, same rule as `vex_guards`.

## 7. Phase 1: reorganize and publish

Scope: move files, publish the site, fix every path reference. **The prose is carried over as is**,
except for the two factual errors in §9 which are fixed in flight. Splitting a file into its target
pages (§4) happens here; rewriting its sentences does not.

1. `git mv docs/{specs,plans,handoffs,reference} agents/`.
2. Split and rename the operator docs into the layout of §4.
3. `docs/README.md` becomes `docs/index.md`; the root `README.md` is rewritten per D6.
4. Update every reference to a moved path. Measured blast radius outside the archives:
   - `AGENTS.md` (10 mentions), `README.md` (5), `SECURITY.md` (1)
   - `deploy/.env.example` (1), `.github/workflows/validate.yml` (1 comment)
   - `packages/crawler/pyproject.toml` (3 pytest marker descriptions)
   - `packages/crawler/src/mulewatch/adapters/mule_ec/{codes,codec}.py`,
     `.../application/run_download_cycle.py`, `.../merge/__init__.py`,
     `.../adapters/persistence_sqlite/connection.py`,
     `packages/crawler/tests/adapters/mule_ec/test_codes.py`,
     `packages/crawler/tests/integration/conftest.py`
   Inside `agents/`, relative links between archives survive the move (they move together); prose
   mentions of the form `docs/specs/...` are rewritten mechanically.
5. Add `zensical.toml` and `.github/workflows/docs.yml`; enable GitHub Pages in "GitHub Actions"
   mode on the repository.
6. Amend `AGENTS.md` per §11.

Phase 1 is done when the site is live at the D9 URL, every internal link resolves, and
`uv run poe check` is green.

## 8. Phase 2: the rewrite, with the cut list frozen

### 8.1 Duplicates to merge

- **deployment annex C (High-ID)** and **administration § High-ID**: same content twice, the second
  one better. One `high-id.md`.
- **deployment annex D (ports and metrics)** repeats **administration § Prometheus metrics**,
  including the no-authentication warning. The metrics half goes; the "change a port" half moves to
  `settings.md`.
- **administration § Diagnostic après panne**: a symptom-to-action table whose rows mostly link to
  the troubleshooting runbook. Deleted; the troubleshooting pages exist for this.
- **deployment step 7 (Vivre avec le nœud)** repeats **administration § Cycle de vie**, same
  commands. Three gestures stay in `install.md` (stop, update, back up); the rest lives in
  `operate.md`.
- The **"one container, three processes"** callout opens three of four documents, and **"the
  catalog's subject is the file, never the person"** opens four of four. Once each, on the home page.
- The **WebUI read-only guarantee** is explained three times in the administration runbook, one of
  them a historical note about a Docker `:ro` mount that no longer applies. Once, without the
  history (which already lives in `agents/reference/2026-06-22-webui-wal-readonly.md`).

### 8.2 Extractions to their own page

- **deployment annex E, 1.x to 2.0 migration** (about 1 000 words, read once in a node's lifetime,
  currently sitting mid-guide) becomes `migration-1x.md`, announced by a banner at the top of
  `install.md`.
- **administration § Vérifier l'authenticité d'une image** becomes `verify-image.md`.
- **The ProtonVPN 60-second port churn card** moves from general troubleshooting to `high-id.md`,
  where the reader is already in that context.

### 8.3 Outright deletions

- **Every decision date in operator copy**: "depuis le 2026-09-13", "mise à jour 2026-06-29, réduite
  2026-09-13, INVERSÉE pour le crawler 2026-09-16" and friends. The operator reads current state.
  The history is in `agents/specs/`.
- **administration § Limites connues / follow-ups**, about 1 000 words of decision journal, is cut
  to a `limits.md` of roughly fifteen factual lines: the memory spike a migration can hit on a
  large catalog, port-sync never validated on real hardware, completed files accumulating without
  bound, and the hardening the single-container image no longer carries. The reasoning stays in the
  specs.
- **"Le healthcheck lit la sortie de s6-svstat, pas son code de retour"**: not a symptom, a note for
  someone writing their own probe. Moves to `contributing/architecture.md`.
- **"Lancer une commande ponctuelle dans une image"**: a development tool, not a fix. Moves to
  `agents/`.
- **The s6 exit-code / `finish` script table** in the administration runbook: internal mechanics; it
  reappears where it is actionable, in `troubleshooting-start.md`.
- **The ten "Retour au guide" blocks** closing each troubleshooting card, and the three "Pour aller
  plus loin" footers: the site navigation does this.
- **Cross-links from operator pages into `docs/reference/` and `docs/specs/`**: those targets leave
  the site, so the links would be dead. Removed, or turned into a neutral mention.

### 8.4 What is not touched

The port 8080 no-authentication warning, in every form it takes. The legality page, already concise
and honest, only tightened. The first-deployment troubleshooting cards, which are the best part of
the current documentation: the symptom / cause / solution format is kept verbatim as a format, only
the sentences get shorter.

### 8.5 Style rules for the rewrite

- **No em dashes or en dashes** anywhere in the published copy (project rule, `~/.claude/rules/no-em-dashes-in-ui.md`).
  Separators are `:`, a period, parentheses, or a middle dot.
- **Bold is for one thing per paragraph**, not five. Current pages bold entire clauses.
- **No nested block quotes**. A callout is a callout; if it needs a callout inside it, it is two
  paragraphs.
- Sentences that state what the reader does, then what they should see. Not the reverse.

### 8.6 Expected size

About 12 000 words today. About 7 000 after, spread over pages that are read whole rather than
scrolled. Plus the translation of `contributing/architecture.md` and `contributing/testing.md` (about 4 900 words).

## 9. Two documentation bugs fixed in flight

Both in the current operator docs, both blocking for a reader who followed the deployment guide
(downloaded a ZIP; no repository clone, no `uv`):

1. `administration.md § Outils de catalogue` documents `uv run python -m mulewatch.merge ...` and
   `uv run python -m mulewatch.compact ...`.
2. `troubleshooting.md § Valider la configuration` documents
   `uv run python -m mulewatch validate-config`.

Both must be `docker compose exec mulewatch python -m ...` for an operator. Fixed during phase 1.

## 10. Out of scope

- **Multilingual content.** Technically available today (§3), deliberately not taken: the cost is
  keeping two copies of 12 000 words in sync forever, not a missing feature. Adding English later
  costs one config file, one build step, and the translation itself.
- **A custom domain.** Default GitHub Pages domain, per D9.
- **Site versioning** (`mike` style, one site per release). The site follows `main`.
- **A third-party publishing action.** The official `actions/upload-pages-artifact` plus
  `actions/deploy-pages` path is used instead: this repository signs and attests its image, so
  adding an unaudited third-party action to a publishing workflow would be incoherent.
- **Rewriting the archives** under `agents/`. They keep their original language and content; only
  path mentions are updated.

## 11. AGENTS.md amendments

1. The orientation section points at `agents/handoffs/`, `agents/specs/`, `agents/plans/`,
   `agents/reference/`.
2. The language rule gains: new docs under `agents/specs/`, `agents/plans/` and `agents/handoffs/`
   stay in English; **everything under `docs/` is French** (D2), as that directory is the published
   site.
3. A new invariant: `docs/` is the Zensical root and is published in full. Anything that should not
   be published does not go there.

## 12. Risks and what is not validated

- **Zensical is young** (first release 2025-11). Pinned to an exact version in the workflow; a
  breaking change is a pin bump, not a surprise on a push to `main`.
- **Nothing about the site is validated against real hardware**, and nothing needs to be: the
  published site touches no node, no image and no runtime path.
- **Anchor links break.** Every `#anchor` link that currently points into a runbook section changes
  when pages split. Phase 1 must sweep them, and a dead-link check in the docs workflow is the
  cheapest guard; Zensical's build reports unresolved links.
- **The rewrite is where content can be lost.** The cut list in §8 is exhaustive and frozen: any
  further deletion during phase 2 goes back to the operator before it happens.
