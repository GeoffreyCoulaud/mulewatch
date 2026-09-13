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
read only the annex that concerns you, each of which describes what it adds to this path.

---

## 1. What you need

- **A machine that stays on.** An old PC or a mini-PC is enough: a node is only useful if it watches
  continuously. You will not need to touch it once started.
- **A permanent Internet connection.**
- **About 2 GB of free RAM** and **about 5 GB of free disk** to start with. The catalog then grows
  slowly, and downloaded files accumulate on top of that: see
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

**Checkpoint.** From that folder, type:

```
ls
```

The listing must contain **`compose.yaml`** (on Windows/PowerShell, `ls` prints a table: look for
`compose.yaml` in the `Name` column). Otherwise you are not in the right folder: move into the
working folder (the one containing `compose.yaml`) and try again.

---

## 4. Your password

Your working folder contains a file called `.env.example`. **Make a copy of it named `.env`**, open
that copy in a text editor, and replace the `change-me` value of:

- `AMULE_EC_PASSWORD`: a password of at least **12 characters**, of your own choosing. It links the
  crawler to the eMule client; write it down somewhere.

Leave everything else as it is (the other values only matter for the annex variants). Do not leave
`change-me` in place: it is a plaintext password, so an open door.

> The `.env` file starts with a dot, so the macOS Finder and some Linux file managers **hide** it.
> The most reliable way everywhere is to create and edit it from the terminal:
> `cp .env.example .env`, then `nano .env` (macOS/Linux) or `notepad .env` (Windows).

---

## 5. Start it

From your working folder:

```
docker compose up -d
```

On the very first run, Docker downloads the images: this can take a few minutes depending on your
connection.

**Checkpoint.** Once the command returns, type:

```
docker compose ps
```

You should see **two services**, each with a status starting with `Up`: `crawler` and `amuled`.
(`crawler` may turn to `Up (healthy)` after a few seconds: even better.) If a service is
`Restarting` or `Exited`, open the entry
["Un conteneur redémarre en boucle"](troubleshooting.md#un-conteneur-redémarre-en-boucle). If the
start fails on a message about a port already in use, open the entry
["Le port est déjà pris"](troubleshooting.md#le-port-est-déjà-pris).

---

## 6. See your node

Open **<http://localhost:8080>** in your browser: this is the catalog, read only. You should see the
mulewatch dashboard, with your node identifier and the list of target episodes (the **Status**
column reads `none` at first). **If that page loads, your node is running.**

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

- **Updating.** The images do **not** update by themselves: you decide when.

  ```
  docker compose pull
  ```

  ```
  docker compose up -d
  ```

  `up -d` only recreates the containers whose image changed. Your data does not move.

- **Stopping the node.**

  ```
  docker compose down
  ```

  The catalog and the node state live in named Docker volumes: they **persist**. A `down` followed
  by an `up -d` later finds everything again. To **erase everything** (the catalog included) you
  would have to add `-v` to `down`: only do that if you really mean it. Note that `-v` does not
  touch `downloads/`, which is a plain folder on your disk, not a Docker volume.

- **Host reboot.** The containers come back on their own when the host boots (Docker must start as a
  system service). No command to retype.

**Checkpoint.** You can verify this: a `docker compose down` followed by a `docker compose up -d`
finds, on <http://localhost:8080>, everything the catalog had already seen. Your data survives a
shutdown.

> Lifecycle in more detail (diagnosis after a failure, disk planning, reboot):
> [administration runbook, § Cycle de vie & données](administration.md#cycle-de-vie--données).
> If your node was created **before the rename to `mulewatch`** and seems to have lost its catalog
> after an update, the same section explains how to recover the old volumes.

---

## Annex A. Going behind a VPN

To hide your IP from the other eD2k/Kad peers, route amuled through a VPN with the `gluetun`
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

This stack adds two services: `gluetun` itself, and `docker-proxy`, a confined Docker-socket proxy
used by the port-sync of annex C. So `docker compose -f gluetun.compose.yml ps` shows **four**
services instead of two. amuled shares gluetun's network.

---

## Annex B. Catalog-only mode (no downloading)

By default a node downloads the candidates it confidently identifies. If you only want to catalog
and be notified, without any file landing on your disk:

1. In `config/crawler/crawler.yml`, set `download.enabled: true` to **`false`**.
2. Restart from your working folder:

   ```
   docker compose up -d
   ```

Nothing else changes: the same two services start, the same web catalog is served, notifications
still go out. Only the download loop is not wired, so `downloads/incoming` stays empty.

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
| **VPN stack (gluetun), port forwarding** | Set `VPN_PORT_FORWARDING=on` in your `.env` **and** `port_sync.enabled: true` in `config/crawler/crawler.yml`. Your VPN provider must support port forwarding ([gluetun list](https://github.com/qdm12/gluetun-wiki/tree/main/setup/providers)). |

Trade-offs, step-by-step enabling and verification:
[administration runbook, § High-ID](administration.md#high-id-optionnel--devenir-joignable).

---

## Annex D. Ports and metrics

- **Changing the web catalog's port.** In your `.env`, `WEBUI_PORT` (default `8080`). Useful if that
  port is already taken on your machine.
- **Metrics.** The crawler exposes a Prometheus `/metrics` endpoint on the port configured by
  `observability.metrics.port` in `config/crawler/crawler.yml` (default `9090`). **No Prometheus and
  no Grafana ship with the stack**: if you want dashboards, point your own Prometheus at the
  crawler. That port is not published on the host by default, so either attach your Prometheus to
  the stack's `ec` network, or add a port mapping to the `crawler` service.
- **Turning metrics off.** Set `observability.metrics.enabled: false` in
  `config/crawler/crawler.yml`. The crawler and the catalog keep working normally.

Metric details and exposure behind a reverse proxy:
[administration runbook, § Métriques Prometheus](administration.md#métriques-prometheus) and
[§ Exposition derrière un reverse proxy](administration.md#exposition-derrière-un-reverse-proxy).

---

## Minimal glossary

| Term | Meaning |
|---|---|
| **service** | One brick of the node: a container managed by `docker compose` (for example `crawler`, `amuled`). |
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
