"""Fabrique de l'application Starlette (spec webui — Task 11).

``build_app`` câble les adaptateurs (SQLite, YAML, templates) et enregistre
toutes les routes. Les handlers sont des fermetures capturant les dépendances
— pas de ``app.state``.
"""

from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from catalog_webui.adapters.catalog_read import CatalogReader
from catalog_webui.adapters.db import open_ro
from catalog_webui.adapters.local_read import LocalReader
from catalog_webui.adapters.matching_read import MatchingExplainer
from catalog_webui.adapters.targets_read import load_targets
from catalog_webui.domain.coverage import coverage_for
from catalog_webui.domain.format import ed2k_link, short_hash
from catalog_webui.domain.views import (
    FileDetailDisplay,
    FileRowDisplay,
    TargetCoverageRow,
)


def build_app(
    *,
    catalog_db: Path,
    local_db: Path,
    targets: Path,
    matcher: Path,
    templates_dir: Path,
    static_dir: Path,
) -> Starlette:
    """Construit et retourne l'application Starlette câblée."""

    templates = Jinja2Templates(directory=str(templates_dir))
    target_segments = load_targets(targets)
    explainer = MatchingExplainer(matcher_yaml=matcher, targets_yaml=targets)

    # Titre par target_id (accès rapide)
    _title_by_id = {seg.target_id: seg.title for seg in target_segments}

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def handle_health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def handle_dashboard(request: Request) -> Response:
        catalog = CatalogReader(open_ro(catalog_db))
        coverage_data = catalog.target_coverage()
        local = LocalReader(open_ro(local_db))
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
        target_param = request.query_params.get("target")
        tier_param = request.query_params.get("tier")
        verdict_param = request.query_params.get("verdict")
        query_param = request.query_params.get("q")
        page_raw = request.query_params.get("page", "1")
        try:
            page = int(page_raw)
        except ValueError:
            page = 1

        catalog = CatalogReader(open_ro(catalog_db))
        file_rows = catalog.list_files(
            target=target_param,
            tier=tier_param,
            verdict=verdict_param,
            query=query_param,
            page=page,
        )

        display_rows = [
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
                ed2k_link=ed2k_link(row.ed2k_hash, row.filename, row.size_bytes),
            )
            for row in file_rows
        ]

        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows},
        )

    async def handle_file_detail(request: Request) -> Response:
        ed2k_hash = request.path_params["ed2k_hash"]

        catalog = CatalogReader(open_ro(catalog_db))
        detail = catalog.file_detail(ed2k_hash)

        if detail is None:
            return PlainTextResponse("Not Found", status_code=404)

        # Précalcul du lien eD2k depuis la dernière observation
        last_obs = detail.observations[-1] if detail.observations else None
        if last_obs is not None:
            link = ed2k_link(detail.ed2k_hash, last_obs.filename, last_obs.size_bytes)
        else:
            link = ""

        # Explication depuis la config courante
        explanation_target_id: str | None = None
        explanation_rules_fired: tuple[str, ...] = ()
        explanation_tokens_matched: tuple[str, ...] = ()
        explanation_config_note: str = ""

        if detail.decision is not None and last_obs is not None:
            explanation = explainer.explain(
                filename=last_obs.filename,
                size_bytes=last_obs.size_bytes,
                media_length_sec=None,
                bitrate_kbps=None,
                target_id=detail.decision.target_id,
            )
            if explanation is not None:
                explanation_target_id = explanation.target_id
                explanation_rules_fired = explanation.rules_fired
                explanation_tokens_matched = explanation.tokens_matched
                explanation_config_note = "Évalué contre la configuration actuelle"

        decisions = (detail.decision,) if detail.decision is not None else ()
        explanation_notes = (explanation_config_note,) if explanation_config_note else ()

        display = FileDetailDisplay(
            ed2k_hash=detail.ed2k_hash,
            size_bytes=detail.size_bytes,
            aich_hash_display=detail.aich_hash if detail.aich_hash is not None else "—",
            observations=detail.observations,
            decision=detail.decision,
            decisions=decisions,
            verifications=detail.verifications,
            ed2k_link=link,
            explanation_target_id=explanation_target_id,
            explanation_rules_fired=explanation_rules_fired,
            explanation_tokens_matched=explanation_tokens_matched,
            explanation_config_note=explanation_config_note,
            explanation_notes=explanation_notes,
        )

        return templates.TemplateResponse(
            request,
            "file_detail.html",
            {"file": display, "title_by_id": _title_by_id},
        )

    async def handle_target(request: Request) -> Response:
        target_id = request.path_params["target_id"]
        catalog = CatalogReader(open_ro(catalog_db))
        file_rows = catalog.list_files(
            target=target_id,
            tier=None,
            verdict=None,
            query=None,
            page=1,
        )

        display_rows = [
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
                ed2k_link=ed2k_link(row.ed2k_hash, row.filename, row.size_bytes),
            )
            for row in file_rows
        ]

        return templates.TemplateResponse(
            request,
            "files.html",
            {"rows": display_rows},
        )

    async def handle_node(request: Request) -> Response:
        local = LocalReader(open_ro(local_db))
        node_state = local.node_state()

        return templates.TemplateResponse(
            request,
            "node.html",
            {"node_state": node_state},
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
            Mount("/static", StaticFiles(directory=str(static_dir))),
        ]
    )
