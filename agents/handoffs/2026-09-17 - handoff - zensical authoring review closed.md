# Handoff: Zensical authoring review, closed

Date: 2026-09-17
Committed straight to `main` (docs-only diffs, per the AGENTS.md exception), except the glossary
change, which went through PR #67 because it also touched `zensical.toml`.
Previous: `2026-09-17 - handoff - automatic glossary tooltips.md`

## Current state

The authoring review of https://zensical.org/docs/authoring is finished. Seven ideas were raised,
**four shipped, three were dropped after inspecting the actual content**. The site builds clean
under `--strict`.

Shipped: automatic glossary tooltips (PR #67), code-block languages, VPN coverage on the advanced
pages plus the French pass, and code annotations + card grid + per-page descriptions.

## What was done, after the glossary

- **Code-block languages.** 18 command blocks were unlabelled and rendered without highlighting;
  they are `bash` now, and the three `sh` blocks in `verify-image.md` were unified onto it. The
  four blocks that hold *program output* (a compose error, three log lines) deliberately keep no
  language. Two blocks showing file contents gained a `title=`.
- **VPN coverage.** `troubleshooting.md` showed 22 `docker compose` commands in direct form and
  never told a VPN reader to add `-f gluetun.compose.yml`, while its sibling page carries exactly
  that note. Both it and step 6 of the migration guide now do.
- **French pass.** Eleven code comments were still English, four of them inside the two copies of
  the s6 process block — the same block read in one language on one page and the other language on
  the next.
- **Code annotations**, on the manual retry recipe in `troubleshooting.md`.
- **Card grid** on the home page, and a `description` front-matter key on all fifteen pages.

## Learned pitfalls

- **Code annotations are paired by JavaScript in the browser, not at build time.** The built HTML
  legitimately contains a literal `<span class="c1"># (1)!</span>` and a plain `<ol>`; grepping the
  static output for `md-annotation` finds nothing and proves nothing. I concluded the feature was
  broken on that basis and was wrong. The real requirements, read out of the theme bundle: a
  `.highlight` element whose **next element sibling** is the `<ol>`, the `content.code.annotate`
  feature enabled, and markers inside a Pygments comment span (`.c`, `.c1`, `.cm`) matching
  `(\d+)!`. All four hold here. **Not seen in a browser** — Chrome tooling was unavailable; verify
  visually if you touch this.
- **Inspect the content before believing an idea.** Three of seven suggestions died on contact:
  embedding `deploy/` files (no doc copies them — the duplication did not exist), Zensical
  *directives* (two site variants where two tabs would do), and **content tabs for the VPN stack**.
  That last one looked strongest and was the weakest: the VPN-only blocks are `gluetun` commands,
  and `gluetun` does not exist in the direct stack, so the "Pile directe" tab would be empty. The
  reverse case is covered by one sentence per page. Tabs would also have needed `content.tabs.link`
  in `zensical.toml`, hence a PR, for a large noisy diff that saves one flag.
- **Counting fence lines is not counting blocks.** "43 unlabelled blocks" was 43 fence *lines*;
  there were 22 blocks, and only 18 of them wanted a language.
- **Length-thresholded greps kept missing short comments.** Three successive passes each missed
  English comments (`# stop it`, `# status`) because of a minimum-length filter. Reading the
  surrounding block found the rest.

## Not validated against real hardware

Nothing here touches the runtime. `docs-build --strict` is green.

`uv run poe test` still fails on `tests/adapters/mule_ec/test_transport.py::
test_connect_refused_raises_connect_error`, **pre-existing and macOS-only** (the test wants a
deterministic RST, which its own comment marks as Linux behaviour; macOS drops the packet and the
connection times out). CI on Linux is green — it passed on PR #67. Still worth a decision: mark it
Linux-only, or accept both errors.

## Suggested next step

The authoring review is closed; nothing from it is left. Open items found along the way, neither
urgent:

1. The macOS-only test above.
2. `settings.md` and `migration-1x.md` each show one direct `docker compose` command without a VPN
   note. Both pages mention the VPN stack elsewhere, so this is minor — unlike `troubleshooting.md`
   with its 22 commands, which is why that one was fixed and these were left.
