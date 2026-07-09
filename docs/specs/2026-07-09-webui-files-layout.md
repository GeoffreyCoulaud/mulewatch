# WebUI `/files` layout: wider viewport + tidy cells

- Status: draft (awaiting operator approval)
- Date: 2026-07-09
- Branch: `feat/webui-files-layout`
- Scope: `webui` in-process viewer only; read-only, no behavioural change to the crawler

## 1. Problem

The `/files` explorer is a 9-column table inside a `body { max-width: 1100px }` container. Cells
wrap freely (`td` has no `white-space` constraint), so long content breaks mid-value and rows grow
tall and ragged. Four offenders drive it:

- **Filenames** — arbitrarily long (e.g. the canonical Teletoon rip is ~118 chars).
- **Datetimes** — short but breakable, so `2026-07-09 01:02Z` can split across two lines.
- **Multi-target cells** — a whole-episode file carries two decisions, joined with `" · "`
  (`092A / S02E41A · 092B / S02E41B`), which wrap at arbitrary points.
- **Episode titles** — long French titles, also joined with `" · "` for multi-segment files.

The same table is served by two routes (`handle_files` → `/files`, `handle_target` →
`/targets/{id}`); both share `files.html` and the `FileRowDisplay` view-model, so one fix covers
both.

## 2. Decisions

Confirmed with the operator during brainstorming:

1. **Approach C (hybrid):** atoms never break (`nowrap`), multi-value cells stack vertically
   (one segment per line), and the two truly-unbounded fields (name, title) truncate with an
   ellipsis and reveal the full text on hover / detail page.
2. **Wider body**, viewport-aware, capped in `rem` (follows browser zoom): `min(100rem, 94vw)`.
3. **Structured** multi-value rendering (not CSS-only ellipsis): the view-model exposes a
   per-decision list so the template stacks Target and Title aligned per segment. Rationale: the
   operator's complaint is precisely about multi-target / multi-title cells; a CSS-only ellipsis
   would *hide* the second value rather than present it.
4. **Mobile / tablet is out of scope** for now (marginal); "responsive" here means adapting
   cleanly to variable desktop widths, plus horizontal scroll as the narrow-width fallback.

## 3. Design

### 3.1 Body width (global, `app.css`)

```css
body { max-width: min(100rem, 94vw); }
```

The only global change (replaces `max-width: 1100px`). `rem` cap follows browser zoom; the `94vw`
term keeps a healthy margin on smaller desktop windows; the `100rem` cap stops `file_detail` prose
from stretching to unreadable line lengths on ultra-wide screens.

### 3.2 Per-column behaviour (`.files-table`, scoped classes)

| Column(s) | Treatment |
|---|---|
| Hash, Size, Sources, Last seen, Verdict | `white-space: nowrap` — short atoms, never break (fixes the split datetime) |
| Name | `max-width` (~22rem) + `overflow: hidden` + `text-overflow: ellipsis` + `nowrap`; full name in `title=` and on the detail page |
| Tier | `nowrap`; short |
| Target | stacked per decision (§3.3); each line `nowrap` |
| Title | stacked per decision (§3.3); each line ellipsis-truncated + `title=`, `max-width` ~20rem |

A wrapper element around the table carries `overflow-x: auto` so a narrow desktop window scrolls
horizontally instead of crushing columns (mirrors the existing `.console-table` pattern).

### 3.3 Structured multi-value rendering

Rendered stacked and aligned per segment:

```
Name                    Target            Title                            Tier
[Keroro].092.[…].avi    092A / S02E41A    On vous envoie tout notre …      notify
                        092B / S02E41B    Bataille acharnée au badminton
```

**View-model (`domain/views.py`).** Introduce a small precomputed cell and replace the two joined
strings on `FileRowDisplay`:

```python
@dataclass(frozen=True)
class DecisionCell:
    target: str  # "062A / S02E11A", a raw id, or "unidentified"
    title: str   # episode title, or "·"

@dataclass(frozen=True)
class FileRowDisplay:
    ...
    decisions_display: tuple[DecisionCell, ...]  # 0..N, one per current decision
    # tier_display and verdict_display are UNCHANGED (shared-tier / per-file logic stays a string)
```

`decisions_display` is empty when the file has no current decision; the template renders the
literal `"·"` via `{% for %}{% else %}` (the template guard forbids `{% if %}` but allows
`for/else`, already used in `files.html`).

**Builder (`composition/app.py::_to_display_rows`).** `_resolve_target_display` already returns the
per-decision `(target, title)` pairs; stop joining them and wrap each in a `DecisionCell`. The
no-decision branch yields an empty tuple. `tier_display` / `verdict_display` are untouched.

**Template (`files.html`).** The Target and Title `<td>`s iterate `row.decisions_display`, emitting
one block element per decision so segments stack; both columns iterate the same order, so they line
up segment-for-segment.

## 4. Testing (TDD, 100% branch per package)

- View-model / builder tests updated for the new `decisions_display` shape, both sides of each
  branch: 0 decisions (empty tuple, cell renders `"·"`), 1 decision, 2 decisions, a `catalog`-tier
  decision (`"unidentified"` / `"·"`), and a `target_id` no longer in the catalogue (raw id /
  `"·"`). `tier_display` shared-vs-disagree cases already covered stay green.
- No new runtime logic in the template (only `for/else`), so `check_templates` stays satisfied.
- CSS is not unit-tested; the layout is verified by eye against the real catalogue after the change
  (operator's node), per the "verify DB/UI changes in the real container" habit.

## 5. Out of scope

- Mobile / tablet layouts (no stacked-card breakpoint).
- Any change to what data is shown, pagination, sorting, or filtering.
- The other tables (`dashboard`, `node`, `console`) — they inherit only the wider body; their
  column treatment is unchanged.
