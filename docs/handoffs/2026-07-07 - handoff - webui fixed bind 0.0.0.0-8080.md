# Handoff: webui bind fixed at `0.0.0.0:8080` (host/port config removed)

Date: 2026-07-07
Branch: `refactor/webui-fixed-bind`
Tag (suggested): `v0.29.0-webui-fixed-bind`

## Why

An operator running an older deployment (a `crawler.yml` with **no** `webui:` section) could
not reach the webui at `http://localhost:8080` even though the `crawler` container was `healthy`
and published `0.0.0.0:8080->8080`. Root cause: `WebuiConfig`'s default bind host was
`127.0.0.1`, so uvicorn listened only on the container's **internal loopback**. Docker cannot
route a published port to a container's loopback (the container netns is the isolation boundary),
so the forward hit nothing and the container reset the connection (`curl: (56)`). The healthcheck
still passed because `curl http://localhost:8080/health` runs **inside** the container, where
`localhost == 127.0.0.1` matched the bind. That masked the unreachability.

The `host`/`port` knobs earned nothing in the Docker-only distribution: exposure is already
governed at the layers that enforce it (the compose published port + networks, and `webui.enabled`
for on/off). A `127.0.0.1` default was an active footgun that broke the only supported deployment,
and it even contradicted `base.compose.yml`'s own comment ("bound ... 0.0.0.0:8080").

## What was built

The webui bind is now **fixed at `0.0.0.0:8080` in the composition layer**, not configurable.

- `adapters/config/crawler_config.py`: `WebuiConfig` carries **only** `enabled`. `host`/`port`
  removed. `_parse_webui(raw)` reads only `enabled` (default True); any other key, including a
  legacy `host`/`port`, is **silently ignored** (operator decision 2026-07-07: no fail-fast, an
  old config keeps starting). The now-unused `env` param was dropped from `_parse_webui`.
- `composition/app.py`: module constants `_WEBUI_BIND_HOST = "0.0.0.0"` / `_WEBUI_BIND_PORT = 8080`
  (comment records WHY 0.0.0.0 and WHY 8080-not-80: port 80 is privileged, needs
  `CAP_NET_BIND_SERVICE`, which collides with the `cap_drop: ALL` + non-root hardening floor).
  `WebuiServerFactory` is now `Callable[[Starlette], WebuiServer]`; `default_webui_server_factory`
  and `_start_webui` drop the host/port args and use the constants.
- `webui/composition/app.py`: the `_SecurityHeadersMiddleware` docstring no longer claims "the
  127.0.0.1 bind limits exposure" (false); it now states there is no built-in auth and exposure
  is governed by compose + reverse proxy / VPN.
- `deploy/base.compose.yml` + `deploy/config/crawler/crawler.yml` + `docs/runbooks/administration.md`:
  comments/table updated; the shipped config's `webui:` block keeps `enabled: true` only.

## Decisions

- Bind fixed at `0.0.0.0:8080`, not `:80` (privileged-port vs `cap_drop: ALL` collision).
- Legacy `webui.host`/`webui.port` keys are **ignored silently**, not rejected (operator call).
- The dated plan `docs/plans/2026-07-06-monolith-consolidation.md` was left untouched (historical
  artifact; the two "webui port" mentions in the monolith spec refer to the *published* host port,
  not the removed knobs).

## Verification

`uv run poe check` green: lint-all + per-package tests at 100% branch coverage (matching 234,
crawler 990, verifier 176). The real `default_webui_server_factory` is asserted to build a
`uvicorn.Server` with `config.host == "0.0.0.0"` / `config.port == 8080`; the wiring test asserts
the `webui serving on 0.0.0.0:8080` log via `caplog`.

## NOT validated against real hardware

- No container image rebuild was run here (sandbox has no real Docker / veth). The behavior is
  identical to the workaround the operator already confirmed live (adding `webui.host: 0.0.0.0`
  reached the webui from the host), so a rebuilt image with the **new default** and NO `webui:`
  host key is expected to be reachable at `http://<host>:${WEBUI_PORT:-8080}` out of the box.
  Confirm on a real node: `docker compose up -d --build crawler`, then `curl http://localhost:8080/health`.

## Suggested next step

Merge via PR (touches code/config/tests, so the `validate / gate` check must run). Then, on a real
node, rebuild the crawler image and confirm the webui is reachable with a `crawler.yml` that has no
`webui.host`. Multi-target matching tuning on the real catalog remains the deferred larger item.
