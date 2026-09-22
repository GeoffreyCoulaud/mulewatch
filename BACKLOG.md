# Backlog

Improvements the project intends to make, one entry each, at most two lines.

Not a changelog and not a record: an entry is **deleted** when it ships or when it is dropped,
never annotated with what became of it. The history is in git and in `agents/handoffs/`.

An agent adds an entry only after the operator has agreed to it. An entry that needs more than
two lines to state has a spec; link the spec and keep the entry short.

- **amuleapi: multi-search, Kad `more`, disk space.** Concurrent searches instead of one at a
  time, `POST /search/{id}/more`, and free space off `GET /status`. `agents/specs/2026-09-22-amuleapi-migration.md` §8.1
- **Matching rules on media duration.** `attr_between` on `duration_sec`, once the fill rate of
  the API's `media` field is measured on the real catalog. Same spec, §8.2
- **SSE instead of polling.** Worth it for `shared_added` (completion signal) above all; the
  rest is latency we do not need. Same spec, §8.3
- **Union of filenames per hash in the decision layer.** A decision is about the file, so
  evaluate every known name and retract only when none match. Same spec, §8.4
