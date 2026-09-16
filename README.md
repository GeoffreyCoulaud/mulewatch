# mulewatch

mulewatch continuously watches the eMule network (eD2k + Kad) to recover lost media. Its first
mission: the French dub of *Keroro mission Titar* (Teletoon, 2008), today largely impossible to
find.

These episodes have not quite disappeared. They resurface intermittently, for as long as a holder
stays connected, then vanish again. A manual search almost always comes at the wrong moment. A
permanent, distributed watch does not: several searchers each run a node, each one searches
tirelessly, catalogues what it crosses, and raises an alert the moment a missing episode shows up.

> **Ethics.** The catalog's subject is the file, never the person. mulewatch tracks nobody and
> tries to deanonymize nobody: it records that a file exists, where and when it was seen, and
> nothing more.

## Running a node

Budget about fifteen minutes once Docker is installed. Installing Docker and pulling the images
come on top, depending on your connection. Installing Docker is by far the hardest step: the rest
is one command and one password to choose.

A node searches, catalogues, notifies, and downloads what it confidently identifies. Downloading
can be turned off (`download.enabled: false` in `config/crawler/crawler.yml`) for a catalog-only
node. Once started, the web catalog is available on http://localhost:8080.

The full walkthrough (secrets to fill in, the VPN variant, High-ID) lives in the
[deployment guide](docs/runbooks/deployment.md). When something goes wrong, the
[troubleshooting guide](docs/runbooks/troubleshooting.md) goes from symptom to cause to fix.

## How it works

A node repeats a simple loop:

1. **Search.** It embeds an eMule client and continuously runs searches derived from the list of
   target episodes, on both of eMule's networks.
2. **Evaluate.** Every file seen is confronted with that list (titles, numbers, broadcast dates)
   and gets a confidence tier.
3. **Catalog.** Everything seen is recorded: content fingerprint, filename, size, moment of the
   encounter.
4. **Notify.** When a missing target appears, a notification goes out with the `ed2k://` link to
   fetch it.
5. **Download.** A confidently identified candidate is queued on the eMule client, which writes
   the finished file into your output directory. Nothing opens it, and mulewatch never inspects
   its contents: judging whether the file really is the episode is yours to do.

The catalogs of several nodes **merge without conflict**: since each file is identified by its
content fingerprint, two searchers who see the same file write the same row.

## For developers

Python >= 3.13, `uv` workspace, Clean/Hexagonal architecture, `mypy --strict`, strict TDD (the
tests are the spec), 100 % branch coverage per package.

```bash
./scripts/setup-dev.sh   # install the environment (uv sync --dev) and the pre-push hook
uv run poe check         # the full gate: lint, types, SQL, tests
```

- Design: [crawler MVP spec](docs/specs/2026-06-10-crawler-mvp-design.md)
- Tests: [testing guide](docs/testing-guide.md)
- Deployment: [deployment guide](docs/runbooks/deployment.md)
- Ethics and privacy: [legality and privacy](docs/legal-and-privacy.md)

---

mulewatch is a generic tool. *Keroro mission Titar* (French dub) is its first mission.
