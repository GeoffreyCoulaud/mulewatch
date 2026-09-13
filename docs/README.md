# Documentation: mulewatch

`mulewatch` continuously watches the eMule network (eD2k + Kad) to recover the lost episodes of the
French dub of *Keroro mission Titar*, cataloguing the available metadata along the way. Design
constraint: **the catalog's subject is the file, never the person** (no tracking, no
deanonymization).

This documentation is organised **by audience**. Pick your entry point:

## Operator / node host

You want to **deploy and run** a node (homelab, server). *Honest prerequisite: this assumes you are
comfortable with a **terminal** and **Docker** (Linux/server oriented); the default **Low-ID** state
is enough to contribute.*

- **[Deployment runbook](runbooks/deployment.md)**: *bring up* the `docker compose` stack and see it
  run. Seven steps, then the annexes (VPN, High-ID, catalog-only mode), secrets, first boot, Low-ID.
- **[Administration runbook](runbooks/administration.md)**: *operate and tune* a running node.
  Lifecycle, optional High-ID, Prometheus metrics, container hardening, catalog tools
  (merge/compact/validate), known limits.
- **[Troubleshooting runbook](runbooks/troubleshooting.md)**: *fix a concrete problem*, whatever
  your level. Symptom, cause, fix.
- **[Legality, privacy, ethics](legal-and-privacy.md)**: what your node catalogues and stores (and
  what it does not), the legal risk stated honestly, what a VPN really protects. Read this before
  deploying a public node.

## Collaboration between searchers

`mulewatch` is designed so that **each searcher deploys their own node**; there is **no central
hub** and that is deliberate (a non-goal for v0.x). Collaboration happens **offline**, by sharing
SQLite databases (`catalog.db`) between searchers who each run their own node.

**Architecture:**
- Each searcher hosts **one complete instance** (a crawler, which also serves the read-only webui
  in-process, plus an amuled). Each instance owns its own `catalog.db`.
- Instances **do not know about each other** and never synchronise.
- To share your findings: send your `catalog.db` (shared drive, git LFS, Nextcloud, any channel) to
  another searcher, who **merges** it into their catalog with the `merge` tool.

**Merge tool:** every searcher can merge N collected catalogs into one, with
[the `mulewatch.merge` tool documented in the administration runbook](runbooks/administration.md#outils-de-catalogue).
Merging is **idempotent** (re-merging the same file is a no-op) and **safe by default** (no
overwrite without `--force`). Each file is identified by its **eD2k content fingerprint**, so a
merge never creates duplicates.

**Typical sharing cycle:**

1. You catalog locally for N weeks.
2. You export your `catalog.db` (copy it out of the Docker volume, see
   runbooks/administration.md § Planification disque).
3. You exchange it with other searchers over an offline channel.
4. You merge the received catalogs into yours: `python -m mulewatch.merge --output
   catalog-merged.db your-catalog.db catalog-from-X.db catalog-from-Y.db`.
5. You swap your live `catalog.db` for `catalog-merged.db` (stop the crawler, swap, restart).

**What does not exist (at this stage):**
- No discovery protocol for finding other searchers.
- No automatic "another node found a file you are looking for" notification.
- No real-time synchronisation and no central hub.

These may emerge one day if the community grows; for now, manual sharing is plenty.

## Developer / contributor / CI

You **change the code** or set up CI: how to run the test suites (the per-package gate plus the
integration suites), their exact prerequisites, continuous-integration leads, and the architecture
and design decisions.

- **[Testing guide](testing-guide.md)**: every suite (unit + integration), CI leads, diagnostic
  tools.
- **[Architecture and behaviour](architecture.md)**: subsystems, interactions, runtime lifecycles.
- **[Design specs](specs/)**: the authoritative MVP design (17 sections) and the per-subsystem
  designs.
- The **gate** (build/test/lint commands, hard rules) is described in `AGENTS.md` at the repo root.

## History / decision record

The **why** behind the choices, milestone by milestone, and the implementation plans that were
executed.

- **[Handoffs](handoffs/)**: one continuation guide per milestone
  (`<ISO date> - handoff - <context>.md`); the most recent one is the entry point for the current
  context.
- **[Implementation plans](plans/)**: the plans executed in subagent-driven mode.
- **[Reference notes](reference/)**: dated empirical findings (EC field richness, download opcodes,
  amuled completion behaviour).
