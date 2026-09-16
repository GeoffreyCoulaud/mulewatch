# Deploying a mulewatch node

The catalog's subject is **the file, never the person**.

This guide takes you from nothing to a running node. Out of the box the node searches, catalogues,
notifies, and downloads the files it confidently identifies into a `downloads/` folder next to your
compose file. When you are done you will have a web catalog on `http://localhost:8080`.

Your IP address is visible to the other peers of the eMule network: that is how this network
publicly and normally works. What mulewatch does and does not do is detailed in
[legality and privacy](../legal-and-privacy.md); to hide your IP behind a VPN, see annex A.

Follow the seven steps in order: they are enough to get a running node. Each one ends with a
**Checkpoint** telling you what you should see, and which troubleshooting entry to open otherwise.
The variants (VPN, catalog-only, High-ID, ports) are in the annexes: do the seven steps first, then
read only the annex that concerns you, each of which describes what it adds to this path. If you are
**upgrading an existing node from 1.x**, read annex E first: 2.0 is a breaking change and the
migration is done by hand, once.

> **One container, three processes.** Since 2.0 a node is a **single** container. Inside it, the
> [s6](https://skarnet.org/software/s6/) supervisor runs three processes: `amuled` (the eMule
> client), `amuleweb` (aMule's own web UI) and `mulewatch` (the crawler, which also serves the web
> catalog). You normally never need to know that; it matters when you read logs or restart one
> piece, so the runbooks mention it where it shows.

---

## 1. What you need

- **A machine that stays on.** An old PC or a mini-PC is enough: a node is only useful if it watches
  continuously. You will not need to touch it once started.
- **A permanent Internet connection.**
- **About 2 GB of free RAM** (the container is capped at 2 GB) and **about 5 GB of free disk** to
  start with. The catalog then grows slowly, and downloaded files accumulate on top of that: see
  [administration runbook, § Planification disque](administration.md#planification-disque).
- **A way to open a terminal**: the Terminal app on macOS and Linux, PowerShell on Windows.

---

## 2. Install Docker

mulewatch runs in Docker. Install it from the official page, which is kept up to date and valid for
every system: <https://docs.docker.com/get-started/get-docker/>.

- **Windows / macOS**: install Docker Desktop, then **start it** and wait until it reports that it
  is running.
- **Linux**: install Docker Engine (the "Server" choice on that page), then follow its
  post-installation steps so you can use `docker` without `sudo`.

**Checkpoint.** Type:

```
docker compose version
```

You should see a line like `Docker Compose version v2.x.x` (a newer number is fine too). If you get
`command not found` or a `1.x` version, open the entry
["Docker introuvable ou compose v1"](troubleshooting.md#docker-introuvable-ou-compose-v1). If later
on a command answers `Cannot connect to the Docker daemon` (on Windows
`error during connect ...`), the Docker engine is not started: open the entry
["Docker est installé mais ne répond pas"](troubleshooting.md#docker-est-installé-mais-ne-répond-pas).

---

## 3. Create your working folder

1. Open <https://github.com/GeoffreyCoulaud/mulewatch>, click the green **`Code`** button, then
   **`Download ZIP`**.
2. Unzip the downloaded file. Inside there is a **`deploy`** folder: it is the only one you need.
3. **Copy that `deploy` folder** wherever you want to work and rename it as you like, for example
   **`mulewatch`**. That is your **working folder**. The rest of the ZIP is not needed, you can
   delete it.
4. Open a terminal **in that working folder**: right click "Open in Terminal" on Windows; on
   macOS/Linux, `cd` into it.

If you know git, the equivalent alternative is
`git clone https://github.com/GeoffreyCoulaud/mulewatch.git`, then take its `deploy` subfolder as
your working folder.

What that folder holds, and what each piece is for:

| Entry | What it is |
|---|---|
| `compose.yml` | the stack you start (the one used by these seven steps) |
| `gluetun.compose.yml` | the VPN variant (annex A) |
| `base.compose.yml` | the part both stacks share; you do not start this one on its own |
| `.env.example` | the template for your own `.env` (step 4) |
| `crawler.yml` | the node's settings (download on/off, intervals, notifications) |
| `targets.yml`, `matcher.yml` | the episodes being hunted, and the rules that recognise them |
| `amule/` | the eMule client's own state (its `amule.conf`, server list, Kad nodes) |
| `data/` | your catalog: `catalog.db` and `local.db` |
| `downloads/` | `incoming/` for finished files, `temp/` for partial ones |

The last three start out empty (they contain only a `.gitkeep` placeholder) and fill up as the node
runs. **They are plain folders on your disk**, not Docker volumes: you can open `data/catalog.db`
with any SQLite tool, and back the whole working folder up by copying it.

**Checkpoint.** From that folder, type:

```
ls
```

The listing must contain **`compose.yml`** (on Windows/PowerShell, `ls` prints a table: look for
`compose.yml` in the `Name` column). Otherwise you are not in the right folder: move into the
working folder (the one containing `compose.yml`) and try again.

---

## 4. Your password

Your working folder contains a file called `.env.example`. **Make a copy of it named `.env`**, open
that copy in a text editor, and fill in the four required values:

| Variable | What to put |
|---|---|
| `AMULE_EC_PASSWORD` | A password of at least **12 characters**, of your own choosing. It links the crawler to the eMule client inside the container; write it down somewhere. |
| `WEBUI_PWD` | Another password of your own choosing. It guards **aMule's web UI on port 4711** (step 6). |
| `PUID` | Your own user id. On macOS/Linux run `id -u`; on Windows Docker Desktop leave `1000`. |
| `PGID` | Your own group id. On macOS/Linux run `id -g`; on Windows leave `1000`. |

Leave everything else as it is (the other values only matter for the annex variants). Do not leave
`change-me` in place: those are plaintext passwords, so open doors.

`PUID`/`PGID` are what keep `data/`, `amule/` and `downloads/` readable and writable **by you**, from
the host, without `sudo`. The container takes ownership of those folders on every start using
exactly those numbers.

> **All four are mandatory.** The container refuses to start if any one of them is missing: it
> exits immediately, with a single line naming the variable. See
> ["A required variable is missing"](troubleshooting.md#a-required-variable-is-missing)
> if that happens.

> The `.env` file starts with a dot, so the macOS Finder and some Linux file managers **hide** it.
> The most reliable way everywhere is to create and edit it from the terminal:
> `cp .env.example .env`, then `nano .env` (macOS/Linux) or `notepad .env` (Windows).

> **⚠ Port 8080 has no authentication at all.** `WEBUI_PWD` protects port **4711 only**. The
> mulewatch catalog on port **8080** is served with **no password, no login, no CSRF token** — and
> it exposes the catalog, the crawl controls (pause, force a pass, restart) **and a read-only SQL
> console** to anyone who can reach that port. That is by design: authentication is delegated to
> whatever you put in front of it. On a machine reachable from the Internet, put it behind a reverse
> proxy with authentication, or a VPN, or do not publish 8080 at all. See
> [administration runbook, § Exposition derrière un reverse proxy](administration.md#exposition-derrière-un-reverse-proxy).

---

## 5. Start it

From your working folder:

```
docker compose up -d
```

On the very first run, Docker downloads the image: this can take a few minutes depending on your
connection.

**Checkpoint.** Once the command returns, type:

```
docker compose ps
```

You should see **one service**, `mulewatch`, with a status starting with `Up`. After roughly half a
minute it should read `Up (healthy)`: that means the eMule client inside it really is running. If it
is `Restarting` or `Exited`, open the entry
["Un conteneur redémarre en boucle"](troubleshooting.md#un-conteneur-redémarre-en-boucle). If the
start fails on a message about a port already in use, open the entry
["Le port est déjà pris"](troubleshooting.md#le-port-est-déjà-pris).

---

## 6. See your node

Your node serves **two** web pages:

| Address | What it is | Password |
|---|---|---|
| <http://localhost:8080> | **The mulewatch catalog** — the read-only catalog, the crawl controls and the SQL console. | **None.** See the warning in step 4. |
| <http://localhost:4711> | **aMule's own web UI** — transfers, servers, Kad state. Useful to see the eMule side directly. | `WEBUI_PWD` from your `.env`. |

Open **<http://localhost:8080>**: you should see the mulewatch dashboard, with your node identifier
and the list of target episodes (the **Status** column reads `none` at first). **If that page loads,
your node is running.** Its navigation carries an **aMule** link over to the other page.

The catalog is **empty at first** and fills up over the hours, as files are crossed on the network
(some rare targets can take days to reappear: that is the nature of lost media). To follow search
activity, open the **Nodes** page: after the first cycle (a few minutes), it shows the number and
timestamp of the last cycle, which advance on every reload.

Downloaded files land in the **`downloads/incoming`** folder of your working folder (partial files
sit in `downloads/temp` meanwhile). Nothing inspects them: mulewatch never opens a downloaded file,
so checking that a file really is the episode you wanted is up to you.

If your node runs on a remote server, replace `localhost` with that server's IP address or name.

**Checkpoint.** The page <http://localhost:8080> opens and shows the dashboard (node identifier,
target list). If it does not load at all (connection refused), open the entry
["La webui reste vide"](troubleshooting.md#la-webui-reste-vide).

---

## 7. Living with the node

Your node is autonomous. A few useful gestures, all from your working folder:

- **Updating.** The image does **not** update by itself: you decide when.

  ```
  docker compose pull
  ```

  ```
  docker compose up -d
  ```

  `up -d` only recreates the container if the image changed. Your data does not move.

- **Stopping the node.**

  ```
  docker compose down
  ```

  The catalog, the eMule state and your downloads live in **plain folders** of your working folder
  (`data/`, `amule/`, `downloads/`): they **persist**. A `down` followed by an `up -d` later finds
  everything again. There is nothing that `down -v` could erase — to really start over, you delete
  `data/` yourself, and only if you mean it.

- **Backing up.** Copy the working folder (stop the node first, so the SQLite databases are not
  being written to). That is the whole backup.

- **Host reboot.** The container comes back on its own when the host boots (Docker must start as a
  system service). No command to retype.

- **Restarting one process only.** `docker compose restart mulewatch` restarts the container, and
  therefore all three processes — the eMule client loses its eD2k and Kad sessions and has to
  reconnect. To restart just one, address s6 inside the container:

  ```
  docker compose exec mulewatch s6-svc -r /etc/services.d/amuled
  ```

  The three service names are `amuled`, `amuleweb` and `mulewatch`. The webui's restart button (on
  `/controls`) does exactly this for the crawler, which is why the eMule client keeps its sessions
  across it.

**Checkpoint.** You can verify this: a `docker compose down` followed by a `docker compose up -d`
finds, on <http://localhost:8080>, everything the catalog had already seen. Your data survives a
shutdown.

> Lifecycle in more detail (diagnosis after a failure, disk planning, reboot):
> [administration runbook, § Cycle de vie & données](administration.md#cycle-de-vie--données).

---

## Annex A. Going behind a VPN

To hide your IP from the other eD2k/Kad peers, route the node through a VPN with the `gluetun`
container.

**Delta from the main path:**

1. **A VPN provider that supports WireGuard.** This is required: gluetun establishes the tunnel over
   WireGuard.
2. **Three extra variables** in your `.env`:

   | Variable | What |
   |---|---|
   | `WIREGUARD_PRIVATE_KEY` | The WireGuard private key, provided in your VPN's customer area. |
   | `VPN_SERVICE_PROVIDER` | The provider name, for example `protonvpn`, `pia`, `privatevpn`. |
   | `SERVER_COUNTRIES` | The exit country or countries, in English, for example `Switzerland`. |

3. **A different stack file.** Instead of `docker compose up -d`, you use the `gluetun.compose.yml`
   stack, and you add `-f gluetun.compose.yml` to **every** compose command afterwards (`ps`,
   `logs`, `pull`, `down`, and so on):

   ```
   docker compose -f gluetun.compose.yml up -d
   ```

This stack adds exactly one service, `gluetun`, so `docker compose -f gluetun.compose.yml ps` shows
**two** services instead of one. mulewatch has no network of its own there: it shares gluetun's
(`network_mode: service:gluetun`), so all of its traffic — the eMule client's included — goes
through the tunnel, and its two web pages are published **on the gluetun service** instead.

The eD2k port is deliberately **not** published in this stack: inbound connections arrive through
the VPN's forwarded port, not through your host (annex C, route A).

> **Not validated on real hardware.** Sources disagree on whether gluetun's own firewall also needs
> `FIREWALL_INPUT_PORTS=8080,4711` for connections coming from your LAN. If the two pages answer on
> the host itself but not from another machine on your network, that variable is the first thing to
> try.

---

## Annex B. Catalog-only mode (no downloading)

By default a node downloads the candidates it confidently identifies. If you only want to catalog
and be notified, without any file landing on your disk:

1. In `crawler.yml`, set `download.enabled: true` to **`false`**.
2. Restart from your working folder:

   ```
   docker compose up -d
   ```

Nothing else changes: the same container starts, the same three processes run, the same web catalog
is served, notifications still go out. Only the download loop is not wired, so `downloads/incoming`
stays empty.

> This is a **config flag**, not a different stack: there is no compose profile to add or remove,
> in either direction.

---

## Annex C. High-ID (optional)

By default your node is **Low-ID**: it catalogues and downloads, but with fewer direct sources.
Becoming **High-ID** (reachable from the outside) brings more sources and a more efficient search.
It is **not required** to catalog. Two routes, depending on your stack:

| Route | How to enable it |
|---|---|
| **Default stack, open port** | Forward `LISTEN_PORT` (default `4662`, both TCP **and** UDP) from your router to this machine. If you change the port, adjust `LISTEN_PORT` in your `.env`. |
| **VPN stack (gluetun), port forwarding** | Set `VPN_PORT_FORWARDING=on` in your `.env` **and** `port_sync.enabled: true` in `crawler.yml`. Your VPN provider must support port forwarding ([gluetun list](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Under the VPN route, the node now aligns the eMule client on the forwarded port entirely **inside
its own container**: it restarts that one process with `s6-svc`. There is no Docker socket, no
socket proxy and no extra service involved any more.

Trade-offs, step-by-step enabling and verification:
[administration runbook, § High-ID](administration.md#high-id-optionnel--devenir-joignable).

---

## Annex D. Ports and metrics

- **Changing a web port.** In your `.env`: `WEBUI_PORT` (default `8080`, the catalog) and
  `AMULEWEB_PORT` (default `4711`, aMule's UI). Useful if one of them is already taken on your
  machine. These change the **host** side only; inside the container the ports are fixed.
- **Behind a reverse proxy.** If you front port 8080 with a proxy, set `webui.amule_url` in
  `crawler.yml` to the address at which **the browser** can reach aMule's UI — that key is only the
  target of the navigation link, and the browser, not the container, resolves it. Its default is
  `http://localhost:4711`.
- **Metrics.** The crawler exposes a Prometheus `/metrics` endpoint on the port configured by
  `observability.metrics.port` in `crawler.yml` (default `9090`). **No Prometheus and no Grafana
  ship with the stack**: if you want dashboards, point your own Prometheus at the node. That port is
  not published on the host by default, so add a mapping for it to the `mulewatch` service in your
  stack file — and treat it like the catalog, with no authentication of its own.
- **Turning metrics off.** Set `observability.metrics.enabled: false` in `crawler.yml`. The crawler
  and the catalog keep working normally.

Metric details and exposure behind a reverse proxy:
[administration runbook, § Prometheus metrics](administration.md#prometheus-metrics) and
[§ Exposition derrière un reverse proxy](administration.md#exposition-derrière-un-reverse-proxy).

---

## Annex E. Migrating a 1.x node to 2.0

**Read this before `docker compose pull` on an existing node.** 2.0 replaces two images and up to
four services with one image and one service, and moves your data out of Docker's named volumes into
plain folders. There is **no compatibility code and no automatic migration**: you do this by hand,
once, and the old node must be stopped while you do it.

Your working folder is the one holding the old `compose.yaml`. Every command below runs from there.

**Step 1 — stop the old node.** Without `-v`: the named volumes must survive, they are your data and
your rollback.

```
docker compose down
```

(or `docker compose -f gluetun.compose.yml down` if you were on the VPN stack.)

**Step 2 — copy each named volume into its new folder.** The old node kept `catalog.db`,
`local.db` and aMule's state in named volumes; 2.0 reads them from `data/` and `amule/`. Copy, do
not move: leaving the volumes intact is what makes the rollback below possible.

```
mkdir -p data amule downloads/incoming downloads/temp
docker run --rm -v mulewatch_catalog-db:/src -v "$PWD/data":/dst alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_local-db:/src   -v "$PWD/data":/dst alpine sh -c "cp -a /src/. /dst/"
docker run --rm -v mulewatch_amule-state:/src -v "$PWD/amule":/dst alpine sh -c "cp -a /src/. /dst/"
```

Run `docker volume ls` first if you are unsure of the names: a node created before the project was
renamed carries a `deploy_` prefix instead of `mulewatch_`.

The two volumes were mounted at `/data/catalog` and `/data/local`, so each one's root already holds
its database file: copying both into `data/` lands them side by side, which is exactly where 2.0
looks for them. Check that before going on:

```
ls data/     # must show catalog.db and local.db, side by side
```

**Step 3 — move your three config files to the working folder's root.** They used to live in
`config/crawler/`; 2.0 mounts them from next to the compose file.

```
mv config/crawler/crawler.yml config/crawler/targets.yml config/crawler/matcher.yml .
rmdir config/crawler config
```

**Step 4 — edit `crawler.yml`.** Four things to remove, three to change, one to add:

- **remove** the whole `amules:` list — the container holds exactly one eMule client, at an address
  fixed in code (`127.0.0.1:4712`);
- **remove** `download.endpoint:` (same reason);
- **remove** `port_sync.restarter_url:` — there is no Docker proxy to talk to any more;
- **add**, at the top level, `amule_ec_password: ${AMULE_EC_PASSWORD}`;
- **change** `catalog_db_path` to `/data/catalog.db` and `local_db_path` to `/data/local.db`;
- **change** `download.output_dir` to `/downloads`;
- **change** `port_sync.gluetun_control_url` to `http://localhost:8000` (mulewatch now shares
  gluetun's network namespace, so its control server is on localhost).

The shipped `deploy/crawler.yml` of 2.0 is the reference: diff yours against it if in doubt.

**Step 5 — add the new variables to `.env`, then take ownership of the folders.** 2.0 requires four
variables where 1.x required one. Add `PUID`, `PGID` and `WEBUI_PWD` (see step 4 of the main path),
then give the copied data to that uid, since it came out of the volumes owned by someone else:

```
sudo chown -R "$PUID:$PGID" data amule downloads
```

**Step 6 — start the new stack.** The direct stack's file is now `compose.yml`, not `compose.yaml`:

```
docker compose up -d
docker compose ps        # one service, `mulewatch`, Up (healthy) after ~30 s
```

### What carries over, and what does not

- **Your existing `amule.conf` is kept**, except for one key. The container writes the file when it
  is absent, and on every boot it reconciles `ECPassword` in `[ExternalConnect]` with
  `AMULE_EC_PASSWORD`: that variable is the source of truth, so rotating it is just editing `.env`
  and restarting. Every other setting stays yours. Check that `IncomingDir` and `TempDir` point at
  `/downloads/incoming` and `/downloads/temp`, and fix them by hand if they do not:
  ```
  grep -E "^(Incoming|Temp)Dir" amule/amule.conf
  ```
- **Your catalog carries over untouched.** `catalog.db` is append-only and its schema is unchanged
  by this release.
- **The crawler's persisted search backoff resets, once.** The eMule client's internal name is now
  a constant, `amuled`, where 1.x read it from `crawler.yml` (typically `amule-1`). Backoff state
  and scheduler progress are keyed on that name, so the rows written under the old name are ignored
  and the node starts its first 2.0 cycle with a clean slate. This is harmless — the effect is one
  cycle that retries a channel it would otherwise have paused — but it is worth knowing before you
  wonder why the logs look busier than usual on first boot.
- **The `instance` label is gone** from the Prometheus metrics that carried it. If you built a
  dashboard that groups by it, drop that dimension: with one client it was a constant.

### Rolling back

The 1.x image is still published, at its **old name**: `ghcr.io/geoffreycoulaud/mulewatch-crawler`.
That package is frozen at 1.x and **is deliberately never deleted** — it is exactly this rollback
path. To go back: restore your old `compose.yaml` and `config/crawler/` (git, or your backup), point
`IMAGE_TAG` at the 1.x tag you were running, and `docker compose up -d`. The named volumes were only
copied from, never moved or deleted, so the old node finds its data where it left it.

Once you are confident in the new node — give it a few days — you can delete the old volumes with
`docker volume rm mulewatch_catalog-db mulewatch_local-db mulewatch_amule-state`. That is the point
of no return, so do it last, and only after checking that `data/catalog.db` really holds your
history.

---

## Minimal glossary

| Term | Meaning |
|---|---|
| **service** | One brick of the stack: a container managed by `docker compose`. A node is one service, `mulewatch` (two with the VPN, which adds `gluetun`). |
| **s6** | The small supervisor that runs the three processes inside the container (`amuled`, `amuleweb`, `mulewatch`) and restarts one if it dies. |
| **eD2k / Kad** | The two eMule networks being watched: eDonkey2000 (central servers) and Kademlia (decentralised, serverless). |
| **Low-ID / High-ID** | How reachable your node is on eD2k. High-ID = the machine is reachable from the outside (more direct sources). Low-ID works too, just less optimally. |
| **IncomingDir** | The folder where the eMule client writes a finished file. Here it is bind-mounted to `downloads/incoming` in your working folder. |

---

## Going further

- [Administration runbook](administration.md): lifecycle, High-ID, metrics, hardening, catalog
  tools, known limits.
- [Troubleshooting runbook](troubleshooting.md): from symptom to cause to fix.
- [Legality and privacy](../legal-and-privacy.md): what mulewatch does, and above all what it does
  not do.
