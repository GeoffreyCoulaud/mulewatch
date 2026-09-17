# Handoff: automatic glossary tooltips

Date: 2026-09-17
Branch: `docs/glossaire-automatique` — opened as a PR (the diff touches `zensical.toml`, so the
AGENTS.md docs-only exception does not apply).
Previous: `2026-09-17 - handoff - install guide reduction.md`

## Current state

Seven opaque terms (`eD2k`, `Kad`, `High-ID`, `Low-ID`, `IncomingDir`, `s6`, `EC`) now carry their
definition as a hover tooltip on **every** page of the site, without a single line added to those
pages. The glossary itself was restructured from a table into two definition lists: the opaque
jargon, and the everyday words the project uses in a precise sense (`pile`, `service`,
`dossier de travail`), which deliberately get no tooltip.

Measured after the change: 26 tooltips on `contributing/architecture`, 22 on `troubleshooting` and
on `contributing/testing`, 10 on `legal`, down to 0 on the four pages that use none of the terms.

## What was done

- **`zensical.toml`**: enabled `pymdownx.snippets` with `auto_append = ["glossary.md:abbr"]`,
  `base_path = ["docs"]` and `check_paths = true`.
- **`docs/glossary.md`**: rewritten as two definition lists (`def_list` was already enabled), plus
  a `--8<-- [start:abbr]` / `[end:abbr]` block at the bottom holding the seven `*[term]:` lines.
- **Two links to the glossary added** (`index.md`, `install.md`): the page had **zero** inbound
  links and was reachable only through the nav. The everyday vocabulary has no tooltip, so it needs
  them.

## Learned pitfalls

- **`auto_append` does accept section syntax** (`glossary.md:abbr`). The pymdown-extensions
  documentation does not say so; it was established by experiment, not read. This is what makes the
  whole approach possible: without it, the appended file would have to hold nothing but invisible
  definitions, and `docs/` would carry a published, empty page — which the "everything under
  `docs/` is published" invariant forbids.
- **`base_path` resolves from the directory the build runs in, not from `zensical.toml`.** The
  first experiment silently reached into the real `docs/` of the repo instead of the test fixture.
  `uv run poe docs-build` runs from the repo root, so `["docs"]` is correct — but a build launched
  from elsewhere would break. That is why the config carries a comment.
- **`base_path` deliberately excludes the repo root.** The default is `["."]`, which would let any
  page embed any file in the repository — including `deploy/.env` and the rest of a dev node's live
  state. Do not widen it.
- **Section markers do not leak** into the rendered glossary page (verified in the built HTML), and
  **abbreviations never fire inside code**, inline or fenced (also verified). They do fire inside
  headings, table cells and link text; that is a light dotted underline, judged acceptable.
- **Choosing the terms is the real design work, not the plumbing.** An abbreviation fires on every
  occurrence of the word, site-wide. `pile` and `service` are common French words here and were
  left out on purpose: underlined fifty times per page, a tooltip stops helping. If a term is added
  later, count its occurrences first (`grep -ow`).
- `check_paths = true` plus `--strict` means deleting the `abbr` section fails the build instead of
  silently dropping every tooltip.

## Not validated against real hardware

Nothing here touches the runtime — it is a documentation-site build change only.

`uv run poe lint-all` is green. `uv run poe test` fails on
`tests/adapters/mule_ec/test_transport.py::test_connect_refused_raises_connect_error`, **which is
unrelated and pre-existing on macOS**: the test binds a port without listening and expects a
deterministic RST, which the comment in the test itself marks as Linux behaviour. macOS drops the
packet instead, so the connection times out and `EcTimeoutError` is raised where `EcConnectError`
is expected. CI runs on Linux and is unaffected. Left alone — out of scope for this branch, but
worth a decision: either mark it Linux-only or make the assertion accept both errors.

## Suggested next step

The Zensical authoring review that produced this change listed four more items, none started:

1. **Content tabs for the VPN variant** — `pymdownx.tabbed` is configured and used nowhere, while
   eight pages repeat "under the VPN stack, add `-f gluetun.compose.yml`".
2. **43 code fences carry no language**, and ` ```sh ` coexists with ` ```bash `. Adding
   `title="crawler.yml"` would also name the file being shown.
3. **Code annotations** (`content.code.annotate`, enabled, never used) — suited to the
   troubleshooting pages.
4. **A card grid on the home page** instead of the "Par où commencer" table, which reads poorly on
   a phone, and a `description:` front-matter key per page.

Items 1 and 2 are the most valuable. Two ideas were examined and **rejected**: embedding
`deploy/` files into the docs with snippets (no doc actually copies those files — the duplication
being solved does not exist), and Zensical *directives* (building separate direct/VPN site
variants, where two tabs suffice).
