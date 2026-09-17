# Handoff: install guide reduction, and the first use of admonitions

Date: 2026-09-17
Merged to `main` locally (docs-only diff, per the AGENTS.md exception).
Previous: `2026-09-17 - handoff - zensical docs site phase 2.md`
No spec: the cut list was agreed inline in the conversation, then executed. It is reproduced below.

## Current state

The two pages a new operator meets first are roughly half as long, and the site uses admonitions
for the first time.

| Page | Lines | Words |
|---|---|---|
| `install.md` | 223 → 118 | 1595 → 807 (-49 %) |
| `troubleshooting-start.md` | 288 → 153 | 2373 → 1248 (-47 %) |

The line counts fell less than the words because admonition syntax costs lines (a four-space
indented block plus blank lines). No content grew back.

`uv run poe docs-build` (which passes `--strict`) is green. Nine admonitions render as expected,
verified in the built HTML, including the one collapsible `<details class="question">`.

## What was done

The operator's premise was that for one container and four environment variables, the install page
was far too long. It was, and the diagnosis is worth recording: `install.md` held **three documents
stacked** — the actual installation (about fifteen lines of commands), an operations manual (its
section 7, which duplicated `operate.md` lines 25-32), and a reference table of the nine entries of
`deploy/`.

**`install.md`**: seven steps became five. Deleted outright: section 7 "Vivre avec le nœud"
(`operate.md` already covers update, stop and backup), the per-OS Docker breakdown (the official
Docker page's job), the paragraph about the Finder hiding dotfiles, and the redundant "the four are
mandatory" paragraph. Shrunk: the nine-entry `deploy/` table down to the three directories the
reader actually meets. **Kept intact, deliberately**: the four-variable table and the 8080 security
warning — a warning is never a compression target.

**`troubleshooting-start.md`**: the two Docker entries merged into one (they share a cause and a
fix), the `change-me` entry folded into the missing-variable entry it hangs off, and the entries
whose full explanation already lives elsewhere (`limits.md` for the migration OOM, `settings.md`
for port remapping) reduced to the diagnostic plus a pointer.

**Admonitions** (a second pass, after the operator pointed at the Zensical docs). Every extension
needed was already enabled in `zensical.toml` — `admonition`, `pymdownx.details`,
`pymdownx.superfences` — and no page had ever used one. Applied where a block is *a different kind
of thing* than the surrounding prose: the four checkpoints (`success`), the 8080 warning (`danger`),
the 1.x upgrade note (`warning`), IP visibility and "where to run these commands" (`info`), the two
"this is not a failure" reassurances (`note`, `tip`), the OOM remedy (`bug`), and the `change-me`
case as a collapsible `??? question`.

Two blocks moved while being converted, and the move is the point: `change-me` was drowning inside
another entry's bullet list, and the OOM remedy was a doubly indented bullet whose fenced command
was unreadable.

## Learned pitfalls

- **Check inbound anchors before rewriting a page, not after.** Three anchors of
  `troubleshooting-start.md` are linked from other pages and had to survive the cut:
  `#une-variable-obligatoire-manque` (from `troubleshooting.md`),
  `#un-conteneur-redémarre-en-boucle` (from `limits.md`), `#le-port-est-déjà-pris` (from
  `settings.md`). One grep for `install.md#|troubleshooting-start.md#` across `docs/`, `README.md`
  and `deploy/` is the whole check, and it is what shaped which entries could merge.
- **Renumbering steps breaks anchors inside the page too.** `install.md` went from seven steps to
  five, so `install.md#3-créer-votre-dossier-de-travail`, linked from the troubleshooting page,
  became `install.md#3-choisir-vos-mots-de-passe`. `--strict` catches it; remembering it is faster.
- **Do not cut a section before checking the page it defers to actually covers it.** Section 7 was
  safe to delete only because `operate.md` lines 25-32 document update, stop and backup. That was
  verified, not assumed.
- **A link's visible title must match the target page's real `# H1`.** Writing "Réglages, § Ports"
  for a page actually titled "Régler le nœud" builds fine under `--strict` (the anchor was valid)
  and is still wrong for the reader. Strict mode checks anchors, never prose.
- **Turning entries into collapsible `???` blocks costs the heading.** Tempting for a
  symptom-per-entry troubleshooting page, but it would drop them from the table of contents and
  destroy the inbound anchors above. The three anchored entries must stay `###` headings.

## Not validated against real hardware

Nothing here touches a node, an image or a runtime path — Markdown only, and the build is verified
green locally. The site deployment itself is unchanged.

The standing macOS note from phase 1 still applies and is unrelated:
`test_connect_refused_raises_connect_error` fails locally for a platform reason (a socket bound
without `listen` returns RST on Linux, drops SYNs on macOS) and passes on `ubuntu-latest`.

## Suggested next step

Admonitions now exist on exactly two of the fifteen pages. The remaining thirteen still carry their
warnings as bold prose or block quotes — `legal.md`, `limits.md`, `high-id.md` and `vpn.md` are the
obvious candidates, all four being pages whose whole point is a caveat. A sweep would make the site
consistent; it was deliberately left out of scope here rather than bundled into a reduction pass.

The same reduction question has not been asked of `operate.md` (242 lines) or `troubleshooting.md`
(345). Both are reference pages an operator consults rather than reads through, so length hurts
them less — but `operate.md` is on the "Démarrer" path in the nav, right after `install.md`, which
makes it the next reasonable target if the operator wants to keep going.
