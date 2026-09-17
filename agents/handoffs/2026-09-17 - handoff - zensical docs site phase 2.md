# Handoff: Zensical documentation site, phase 2

Date: 2026-09-17
Merged to `main` locally (docs-only diff, per the AGENTS.md exception). Live and deployed.
Spec: `agents/specs/2026-09-17-zensical-docs-site.md` (the cut list is section 8).
Previous: `2026-09-17 - handoff - zensical docs site phase 1.md`

## Current state

The documentation site is complete and live at https://geoffreycoulaud.github.io/mulewatch/.
Fifteen French pages, all rewritten. Phase 2 applied the frozen cut list of spec section 8, then a
sentence-level compression pass over every page.

Operator documentation: **17 827 words before the whole project, 14 115 now (-21 %)**.

Note for anyone reading the spec: its section 8.6 promises "about 12 000 words today, about 7 000
after". **The 12 000 figure was an arithmetic error** made during the discussion phase and carried
into the spec; the four source files actually summed to 17 827. The -40 % target was never
reachable without deleting content, because a large share of these pages is tables, command blocks
and checkpoints, which do not compress. Treat the spec's 8.6 numbers as wrong and this handoff's as
measured.

## What was done

- **Cut list applied in full** (spec 8.1 to 8.3): six duplicate sections merged, three extractions,
  seven deletions. `limits.md` went from a dated decision journal (1080 words) to what can actually
  bite an operator (533).
- **`index.md` was still in English**, missed in phase 1. It is the landing page: rewritten in
  French, and it now carries alone the two framing statements that used to open four pages each.
- **`contributing/architecture.md` and `contributing/testing.md` translated**, so the site is French
  throughout as decided (D2).
- **Compression pass**: install 1791 to 1595, operate 2575 to 2017, legal 1526 to 1253,
  migration-1x 990 to 815, troubleshooting pair about -10 % each.
- **Style rules** (spec 8.5): zero em dashes site-wide, bold reduced to one emphasis per paragraph,
  no nested block quotes.
- **Vocabulary**: the split had left "runbook", "annexe A", "annexe E" and "stack" behind, all
  naming a structure that no longer exists. Zero occurrences remain.

## Learned pitfalls

- **BSD sed has no `\b`.** A `sed 's/\bstack\b/pile/g'` on macOS silently matches nothing and exits
  0. It was reported as a completed normalization and was not. Verify a substitution by grepping
  afterwards, never by the exit code.
- **Anything the compression touches must be diffed at the block level.** The check that caught
  real regressions was extracting fenced code content with
  `awk '/^\s*```/{f=!f;next} f'` and diffing it against the previous revision. A prose-level diff
  is too noisy to see a mangled command.
- **Two pages are not compressible below a floor**: `install.md` and `operate.md` are roughly 40 %
  tables, command blocks and checkpoints. Their word counts moved by 11 % and 22 % while the prose
  itself fell much further.
- **Delegated rewrites need the anchors named explicitly.** Renaming a heading breaks inbound
  `#anchor` links silently in prose but loudly in `poe docs-build --strict`. Three headings are
  referenced from other pages: `operate.md#outils-de-catalogue`,
  `operate.md#planification-disque`, `operate.md#exposition-derrière-un-reverse-proxy`.
- **On a risk page, compression must never soften.** `legal.md` was compressed 19 % with an explicit
  rule that no warning, jurisdiction or caveat may weaken, and the result was verified element by
  element afterwards rather than trusted.

## Not validated against real hardware

Nothing here touches a node, an image or a runtime path. The site is static, built from Markdown,
and the deployment is verified live (every page returns 200, `lang="fr"`).

The phase 1 note still stands: `test_connect_refused_raises_connect_error` fails on macOS for a
platform reason unrelated to this work (a socket bound without `listen` returns RST on Linux and
drops SYNs on macOS). It passes on `ubuntu-latest`.

## Suggested next step

The documentation work is done. Two things are deliberately left open:

- **Bilingual.** Verified feasible on Zensical (spec section 3): two configs, two builds, one site
  tree, per-language nav and search. Staying French is a content-maintenance decision, not a
  technical limit. The current layout supports the switch without moving a file.
- **`docs/` is now published in full.** The invariant is recorded in `AGENTS.md`. Anything that
  should not be public belongs in `agents/`.
