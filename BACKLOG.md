# Backlog

What the project intends to do next. One entry per improvement, at most two lines, stating
what changes and why it is wanted. Every entry stands alone: it carries its own link, so
deleting any other entry leaves it intact.

Not a changelog and not a record. An entry is **deleted** when it ships or when it is
dropped, never annotated with what became of it. The history is in git and in
`agents/handoffs/`.

An agent adds an entry only after the operator has agreed to it.

- **Run several searches at once.** The crawler searches one keyword at a time while the
  daemon can hold several, so covering the keyword list takes longer than it needs to.
  Detail: `agents/specs/2026-09-22-amuleapi-migration.md` §8.1
- **Widen a Kad search instead of starting a new one.** aMule can re-ask the peers it already
  queried for more results, which surfaces files a fresh search would not find.
  Detail: `agents/specs/2026-09-22-amuleapi-migration.md` §8.1
- **Warn when the disk is filling up.** The daemon reports free space on the download volumes
  and nothing watches it, so a full disk is discovered by its consequences.
  Detail: `agents/specs/2026-09-22-amuleapi-migration.md` §8.1
- **Use the runtime advertised with a search result.** An episode lasts about 24 minutes,
  which tells it apart from a film sharing its title, when the network reports the figure.
  Detail: `agents/specs/2026-09-22-amuleapi-migration.md` §8.2
- **Be told when a download finishes, rather than asking.** The crawler re-reads the daemon's
  whole shared-file list on a timer to notice completions the daemon could simply announce.
  Detail: `agents/specs/2026-09-22-amuleapi-migration.md` §8.3
- **Judge a file on every name it is shared under.** One file offered under two names flips
  between matched and unmatched on each pass, rewriting its verdict for nothing.
  Detail: `agents/specs/2026-09-22-amuleapi-migration.md` §8.4
