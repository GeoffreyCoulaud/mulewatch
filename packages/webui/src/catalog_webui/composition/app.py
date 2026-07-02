"""Starlette application factory (webui spec — Task 11).

``build_app`` wires the adapters (SQLite, YAML, templates) and registers all
routes. The handlers are closures capturing the dependencies — no ``app.state``.
"""

import contextlib
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlencode

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from starlette.types import ASGIApp

from catalog_matching.ed2k_link import build_ed2k_link
from catalog_webui.adapters.catalog_read import PAGE_SIZE, CatalogReader
from catalog_webui.adapters.db import open_ro
from catalog_webui.adapters.local_read import LocalReader
from catalog_webui.adapters.matching_read import MatchingExplainer
from catalog_webui.adapters.targets_read import load_targets
from catalog_webui.domain.coverage import coverage_for
from catalog_webui.domain.format import short_hash
from catalog_webui.domain.views import (
    FileDetailDisplay,
    FileRow,
    FileRowDisplay,
    FilesSummary,
    PageNav,
    SchedulerEntry,
    TargetCoverageRow,
)


def _to_display_rows(file_rows: Iterable[FileRow]) -> list[FileRowDisplay]:
    """Convert catalog rows into ``FileRowDisplay`` view-models. Shared dedup between
    ``handle_files`` and ``handle_target`` (code-smell#3 — without it, any column change
    had to be made in two places)."""
    return [
        FileRowDisplay(
            ed2k_hash=row.ed2k_hash,
            short_hash=short_hash(row.ed2k_hash),
            filename=row.filename,
            size_bytes=row.size_bytes,
            source_count=row.source_count,
            last_seen=row.last_seen,
            target_id_display=row.target_id if row.target_id is not None else "—",
            tier_display=row.tier if row.tier is not None else "—",
            verdict_display=row.last_verdict if row.last_verdict is not None else "—",
            ed2k_link=build_ed2k_link(row.filename, row.size_bytes, row.ed2k_hash),
        )
        for row in file_rows
    ]


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
        summary_text = f"Showing all catalogued files — {total:,} catalogued ({matched:,} matched)."
        toggle_label = "Matched only"
        toggle_query = dict(filter_query)  # drop show_unmatched → matched only
    else:
        summary_text = f"Showing matched files only — {matched:,} of {total:,} catalogued."
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


def build_app(
    *,
    catalog_db: Path,
    local_db: Path,
    targets: Path,
    matcher: Path,
    templates_dir: Path,
    static_dir: Path,
) -> Starlette:
    """Build and return the wired Starlette application."""

    templates = Jinja2Templates(directory=templates_dir)
    target_segments = load_targets(targets)
    explainer = MatchingExplainer(matcher_yaml=matcher, targets_yaml=targets)

    # Title by target_id (quick access)
    _title_by_id = {seg.target_id: seg.title for seg in target_segments}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_dashboard(request: Request) -> Response:
        with contextlib.closing(open_ro(catalog_db)) as catalog_conn:
            catalog = CatalogReader(catalog_conn)
            coverage_data = catalog.target_coverage()
        with contextlib.closing(open_ro(local_db)) as local_conn:
            local = LocalReader(local_conn)
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
                    best_tier_display=cov.best_tier if cov.best_tier is not None else "—",
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

        with contextlib.closing(open_ro(catalog_db)) as catalog_conn:
            catalog = CatalogReader(catalog_conn)
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

        display_rows = _to_display_rows(file_rows)
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
            {"rows": display_rows, "nav": nav, "summary": summary},
        )

    async def handle_file_detail(request: Request) -> Response:
        ed2k_hash: str = request.path_params["ed2k_hash"]

        with contextlib.closing(open_ro(catalog_db)) as catalog_conn:
            catalog = CatalogReader(catalog_conn)
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

        if detail.decision is not None and last_obs is not None:
            explanation = explainer.explain(
                filename=last_obs.filename,
                size_bytes=last_obs.size_bytes,
                media_length_sec=last_obs.media_length_sec,
                bitrate_kbps=last_obs.bitrate_kbps,
                target_id=detail.decision.target_id,
            )
            if explanation is not None:
                explanation_target_id = explanation.target_id
                explanation_rules_fired = explanation.rules_fired
                explanation_tokens_matched = explanation.tokens_matched
                explanation_notes = ("Evaluated against the current configuration",)

        decisions = (detail.decision,) if detail.decision is not None else ()

        display = FileDetailDisplay(
            ed2k_hash=detail.ed2k_hash,
            size_bytes=detail.size_bytes,
            aich_hash_display=detail.aich_hash if detail.aich_hash is not None else "—",
            observations=detail.observations,
            decisions=decisions,
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
        with contextlib.closing(open_ro(catalog_db)) as catalog_conn:
            catalog = CatalogReader(catalog_conn)
            file_rows = catalog.list_files(
                target=target_id,
                tier=None,
                verdict=None,
                query=None,
                page=1,
            )
            matched, total = catalog.count_files(
                target=target_id, tier=None, verdict=None, query=None
            )

        display_rows = _to_display_rows(file_rows)
        # No pagination here (target view: we expect few) — empty nav.
        nav = PageNav(page=1, prev_url=None, next_url=None)
        # The target filter already implies a decision, so matched == total here; the
        # summary/toggle is still built via the shared helper so files.html (shared with
        # /files) always has a ``summary`` in context.
        summary = _build_summary(matched, total, False, {"target": target_id})
        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows, "nav": nav, "summary": summary},
        )

    async def handle_node(request: Request) -> Response:
        with contextlib.closing(open_ro(local_db)) as local_conn:
            local = LocalReader(local_conn)
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
            Mount("/static", StaticFiles(directory=static_dir)),
        ],
        middleware=[Middleware(_SecurityHeadersMiddleware)],
    )
