"""Starlette application factory (webui spec — Task 11).

``build_app`` wires the adapters (SQLite, YAML, templates) and registers all
routes. The handlers are closures capturing the dependencies — no ``app.state``.
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp

from catalog_matching.config import MatcherConfig
from catalog_matching.ed2k_link import build_ed2k_link
from catalog_matching.models import TargetSegment
from mulewatch.adapters.persistence_sqlite.reader import ReaderProvider
from mulewatch.ports.crawler_control import CrawlerControl
from mulewatch.webui.adapters.catalog_read import PAGE_SIZE, CatalogReader
from mulewatch.webui.adapters.local_read import LocalReader
from mulewatch.webui.adapters.matching_read import MatchingExplainer
from mulewatch.webui.domain.coverage import coverage_for
from mulewatch.webui.domain.format import human_size, seasonal_id, short_hash, short_timestamp
from mulewatch.webui.domain.views import (
    FileDetailDisplay,
    FileRow,
    FileRowDisplay,
    FilesSummary,
    PageNav,
    SchedulerEntry,
    TargetCoverageRow,
)


def _resolve_target_display(
    row: FileRow, segment_by_id: Mapping[str, TargetSegment]
) -> list[tuple[str, str]]:
    """Per-decision ``(target_display, title_display)`` pairs for a file row, in the row's
    decision order (by target_id). Empty when the file has no current decision. The
    ``catalog → "unidentified"`` mask is applied per decision (``keroro_large`` is the only
    catalog-tier rule; cf. ``domain.views.FileRowDisplay``)."""
    resolved: list[tuple[str, str]] = []
    for dec in row.decisions:
        if dec.tier == "catalog":
            resolved.append(("unidentified", "·"))
            continue
        seg = segment_by_id.get(dec.target_id)
        if seg is None:
            resolved.append((dec.target_id, "·"))
            continue
        locator = seasonal_id(
            season=seg.season, seasonal_number=seg.seasonal_number, letter=seg.segment
        )
        resolved.append((f"{dec.target_id} / {locator}", seg.title))
    return resolved


def _to_display_rows(
    file_rows: Iterable[FileRow], segment_by_id: Mapping[str, TargetSegment]
) -> list[FileRowDisplay]:
    """Convert catalog rows into ``FileRowDisplay`` view-models — one row per file, with the
    file's (usually two) segment decisions aggregated into each cell, joined with ``" · "``.
    Shared by ``handle_files`` and ``handle_target``."""
    rows = []
    for row in file_rows:
        if row.decisions:
            pairs = _resolve_target_display(row, segment_by_id)
            target_display = " · ".join(target for target, _ in pairs)
            title_display = " · ".join(title for _, title in pairs)
            tier_values = {dec.tier for dec in row.decisions}
            if len(tier_values) == 1:
                tier_display = row.decisions[0].tier
            else:
                tier_display = " · ".join(f"{dec.target_id}: {dec.tier}" for dec in row.decisions)
            verdict_display = row.last_verdict if row.last_verdict is not None else "pending"
        else:
            target_display = "·"
            title_display = "·"
            tier_display = "·"
            verdict_display = "·"
        rows.append(
            FileRowDisplay(
                ed2k_hash=row.ed2k_hash,
                short_hash=short_hash(row.ed2k_hash),
                filename=row.filename,
                source_count=row.source_count,
                target_display=target_display,
                title_display=title_display,
                size_display=human_size(row.size_bytes),
                last_seen_display=short_timestamp(row.last_seen),
                tier_display=tier_display,
                verdict_display=verdict_display,
                ed2k_link=build_ed2k_link(row.filename, row.size_bytes, row.ed2k_hash),
            )
        )
    return rows


def _normalize(raw: str | None) -> str | None:
    """Normalize a query param: whitespace stripped, empty ⇒ ``None``. Without it, an HTML
    select with an empty option sends ``?target=`` → ``""`` → ``dec.target_id = ''`` matches
    NOTHING → 0 results with no message (webui-security#0/filters)."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _page_nav(page: int, n_rows: int, base_path: str, query: dict[str, str]) -> PageNav:
    """Precompute the prev/next links for a page (W-D8: view-model, no template logic).
    We don't have the total count → ``next`` is rendered when the page is FULL (standard
    heuristic; at worst a next click returns an empty page)."""
    prev_url: str | None = None
    next_url: str | None = None
    if page > 1:
        prev = dict(query)
        prev["page"] = str(page - 1)
        prev_url = f"{base_path}?{urlencode(prev)}"
    if n_rows >= PAGE_SIZE:
        nxt = dict(query)
        nxt["page"] = str(page + 1)
        next_url = f"{base_path}?{urlencode(nxt)}"
    return PageNav(page=page, prev_url=prev_url, next_url=next_url)


def _build_summary(
    matched: int, total: int, show_unmatched: bool, filter_query: dict[str, str]
) -> FilesSummary:
    """Precompute the /files summary line + matched/all toggle (W-D8: no template logic).

    The toggle preserves the active filters and drops ``page`` (counts differ between modes,
    so page N may not exist → back to page 1)."""
    if show_unmatched:
        summary_text = f"Showing all catalogued files: {total:,} catalogued ({matched:,} matched)."
        toggle_label = "Matched only"
        toggle_query = dict(filter_query)  # drop show_unmatched → matched only
    else:
        summary_text = f"Showing matched files only: {matched:,} of {total:,} catalogued."
        toggle_label = "Show all catalogued files"
        toggle_query = {**filter_query, "show_unmatched": "1"}
    toggle_url = "/files?" + urlencode(toggle_query) if toggle_query else "/files"
    return FilesSummary(summary_text=summary_text, toggle_label=toggle_label, toggle_url=toggle_url)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth security headers (webui-security#3).

    Jinja2 autoescape already neutralizes XSS and the 127.0.0.1 bind limits exposure by
    default. CSP ``default-src 'self'`` prevents an injected fragment from loading an
    external asset (a net under autoescape). ``X-Content-Type-Options: nosniff`` prevents a
    browser from re-guessing the MIME type. ``Referrer-Policy: no-referrer`` avoids leaking
    the eD2k hash to any third-party asset (paranoia consistent with the project's spirit).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response


# Human status messages shown after a control POST (via the PRG ``?done=<code>`` param). The
# strings avoid em/en-dashes (project UI rule). A ``done`` code absent from this mapping (or no
# ``done`` at all) renders NO banner.
_CONTROL_MESSAGES: dict[str, str] = {
    "force-cycle": "Cycle forced. A new search cycle starts shortly.",
    "paused": "Crawl paused. The current cycle finishes, then the crawler idles.",
    "resumed": "Crawl resumed.",
    "restart": "Restart requested. The service goes offline briefly, then returns.",
}


def build_app(
    *,
    catalog_db: Path,
    local_db: Path,
    matcher_config: MatcherConfig,
    targets: tuple[TargetSegment, ...],
    templates_dir: Path,
    static_dir: Path,
    control: CrawlerControl,
) -> Starlette:
    """Build and return the wired Starlette application.

    ``matcher_config`` + ``targets`` arrive ALREADY PARSED from the caller (``__main__`` for
    the standalone entrypoint, ``CrawlerApp`` in-process later); this module no longer reads
    ``matcher.yml`` / ``targets.yml`` itself. Passing the crawler's own parsed matcher is what
    keeps the explainer's config from drifting from the persisted decisions (spec §8).

    ``control`` is the runtime-control PORT (``CrawlerControl``): the webui depends on the port,
    never on the concrete adapter (composition wires ``LoopCrawlerControl``). Every control POST
    dispatches a thread-safe, fire-and-forget intent to the crawler loop; the webui itself holds
    no write connection (spec §4/§10)."""

    templates = Jinja2Templates(directory=templates_dir)
    target_segments = targets
    explainer = MatchingExplainer(matcher_config=matcher_config, targets=targets)

    # Centralized read-only connection management (spec §7): one reused, thread-affine
    # connection per DB per thread — warm page cache, no per-request cold open. Handlers
    # obtain a connection via ``provider.connection()`` and NEVER close it (the point is
    # reuse); ``quiesce()`` is the seam livrable 3's maintenance swap will use.
    catalog_reader = ReaderProvider(catalog_db)
    local_reader = ReaderProvider(local_db)

    # Title by target_id (quick access)
    _title_by_id = {seg.target_id: seg.title for seg in target_segments}
    # Full segment by target_id (Task 3: seasonal locator + title resolution on /files)
    _segment_by_id = {seg.target_id: seg for seg in target_segments}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_dashboard(request: Request) -> Response:
        catalog = CatalogReader(catalog_reader.connection())
        coverage_data = catalog.target_coverage()
        local = LocalReader(local_reader.connection())
        node_state = local.node_state()

        rows = []
        for seg in target_segments:
            decisions = coverage_data.get(seg.target_id, [])
            cov = coverage_for(seg.target_id, decisions)
            rows.append(
                TargetCoverageRow(
                    target_id=seg.target_id,
                    title=seg.title,
                    status=cov.status,
                    best_tier_display=cov.best_tier if cov.best_tier is not None else "·",
                    file_count=cov.file_count,
                )
            )

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"rows": rows, "node_state": node_state},
        )

    async def handle_files(request: Request) -> Response:
        # Filters: ``param.strip() or None`` (webui-security#0) — a select with an empty option
        # sent ``?target=`` (empty string) that matched 0 results with no message.
        target_param = _normalize(request.query_params.get("target"))
        tier_param = _normalize(request.query_params.get("tier"))
        verdict_param = _normalize(request.query_params.get("verdict"))
        query_param = _normalize(request.query_params.get("q"))
        # Presence of ``show_unmatched`` (any value) opts into the whole catalogue.
        show_unmatched = request.query_params.get("show_unmatched") is not None
        page_raw = request.query_params.get("page", "1")
        try:
            page = int(page_raw)
        except ValueError:
            page = 1
        # ``max(1, ...)`` (webui-security#2) — ``?page=0`` → OFFSET=-50 which SQLite treats as 0.
        page = max(1, page)

        catalog = CatalogReader(catalog_reader.connection())
        file_rows = catalog.list_files(
            target=target_param,
            tier=tier_param,
            verdict=verdict_param,
            query=query_param,
            page=page,
            matched_only=not show_unmatched,
        )
        matched, total = catalog.count_files(
            target=target_param,
            tier=tier_param,
            verdict=verdict_param,
            query=query_param,
        )

        display_rows = _to_display_rows(file_rows, _segment_by_id)
        # Filters shared by the toggle link and the page nav.
        filter_query = {
            k: v
            for k, v in {
                "target": target_param,
                "tier": tier_param,
                "verdict": verdict_param,
                "q": query_param,
            }.items()
            if v is not None
        }
        summary = _build_summary(matched, total, show_unmatched, filter_query)

        # Precomputed prev/next links (webui-security#1); the nav preserves ``show_unmatched``.
        nav_query = dict(filter_query)
        if show_unmatched:
            nav_query["show_unmatched"] = "1"
        nav = _page_nav(page, len(display_rows), "/files", nav_query)
        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows, "nav": nav, "summaries": (summary,)},
        )

    async def handle_file_detail(request: Request) -> Response:
        ed2k_hash: str = request.path_params["ed2k_hash"]

        catalog = CatalogReader(catalog_reader.connection())
        detail = catalog.file_detail(ed2k_hash)

        if detail is None:
            return templates.TemplateResponse(request, "404.html", {}, status_code=404)

        # Precompute the eD2k link from the latest observation
        last_obs = detail.observations[-1] if detail.observations else None
        if last_obs is not None:
            link = build_ed2k_link(last_obs.filename, last_obs.size_bytes, detail.ed2k_hash)
        else:
            link = ""

        # Explanation from the current config
        explanation_target_id: str | None = None
        explanation_rules_fired: tuple[str, ...] = ()
        explanation_tokens_matched: tuple[str, ...] = ()
        explanation_notes: tuple[str, ...] = ()

        first_decision = detail.decisions[0] if detail.decisions else None
        if first_decision is not None and last_obs is not None:
            explanation = explainer.explain(
                filename=last_obs.filename,
                size_bytes=last_obs.size_bytes,
                media_length_sec=last_obs.media_length_sec,
                bitrate_kbps=last_obs.bitrate_kbps,
                target_id=first_decision.target_id,
            )
            if explanation is not None:
                explanation_target_id = explanation.target_id
                explanation_rules_fired = explanation.rules_fired
                explanation_tokens_matched = explanation.tokens_matched
                explanation_notes = ("Evaluated against the current configuration",)

        display = FileDetailDisplay(
            ed2k_hash=detail.ed2k_hash,
            size_bytes=detail.size_bytes,
            aich_hash_display=detail.aich_hash if detail.aich_hash is not None else "·",
            observations=detail.observations,
            decisions=detail.decisions,
            verifications=detail.verifications,
            ed2k_link=link,
            explanation_target_id=explanation_target_id,
            explanation_rules_fired=explanation_rules_fired,
            explanation_tokens_matched=explanation_tokens_matched,
            explanation_notes=explanation_notes,
        )

        return templates.TemplateResponse(
            request,
            "file_detail.html",
            {"file": display, "title_by_id": _title_by_id},
        )

    async def handle_target(request: Request) -> Response:
        target_id: str = request.path_params["target_id"]
        catalog = CatalogReader(catalog_reader.connection())
        file_rows = catalog.list_files(
            target=target_id,
            tier=None,
            verdict=None,
            query=None,
            page=1,
        )

        display_rows = _to_display_rows(file_rows, _segment_by_id)
        # No pagination here (target view: we expect few) — empty nav.
        nav = PageNav(page=1, prev_url=None, next_url=None)
        # files.html is shared with /files, whose matched/all summary line is meaningless
        # on a target-scoped page — pass an empty tuple so it renders nothing.
        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows, "nav": nav, "summaries": ()},
        )

    async def handle_node(request: Request) -> Response:
        local = LocalReader(local_reader.connection())
        node_state = local.node_state()

        scheduler_entries = tuple(
            SchedulerEntry(key=k, value=v) for k, v in node_state.scheduler.items()
        )

        return templates.TemplateResponse(
            request,
            "node.html",
            {"node_state": node_state, "scheduler_entries": scheduler_entries},
        )

    # ------------------------------------------------------------------
    # Runtime controls (phase P6a): POST intents dispatched to the crawler loop via
    # ``control`` (a ``CrawlerControl`` port). Each POST is fire-and-forget then redirects
    # (PRG) to ``/controls?done=<code>``; GET renders the page + a 0-or-1 message banner.
    # NO CSRF token: consistent with the no-auth, network-trust-boundary posture (spec §12).
    # ------------------------------------------------------------------

    async def handle_controls(request: Request) -> Response:
        done = request.query_params.get("done")
        message = _CONTROL_MESSAGES.get(done) if done is not None else None
        # 0-or-1-element tuple the template iterates with {% for %} (no {% if %}, W-D8).
        messages: tuple[str, ...] = (message,) if message is not None else ()
        return templates.TemplateResponse(request, "controls.html", {"messages": messages})

    async def handle_force_cycle(request: Request) -> Response:
        control.force_cycle()
        return RedirectResponse("/controls?done=force-cycle", status_code=303)

    async def handle_pause(request: Request) -> Response:
        control.pause()
        return RedirectResponse("/controls?done=paused", status_code=303)

    async def handle_resume(request: Request) -> Response:
        control.resume()
        return RedirectResponse("/controls?done=resumed", status_code=303)

    async def handle_restart(request: Request) -> Response:
        control.restart()
        return RedirectResponse("/controls?done=restart", status_code=303)

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    return Starlette(
        routes=[
            Route("/health", handle_health),
            Route("/", handle_dashboard),
            Route("/files", handle_files),
            Route("/files/{ed2k_hash}", handle_file_detail),
            Route("/targets/{target_id}", handle_target),
            Route("/node", handle_node),
            Route("/controls", handle_controls),
            Route("/controls/force-cycle", handle_force_cycle, methods=["POST"]),
            Route("/controls/pause", handle_pause, methods=["POST"]),
            Route("/controls/resume", handle_resume, methods=["POST"]),
            Route("/controls/restart", handle_restart, methods=["POST"]),
            Mount("/static", StaticFiles(directory=static_dir)),
        ],
        middleware=[Middleware(_SecurityHeadersMiddleware)],
    )
