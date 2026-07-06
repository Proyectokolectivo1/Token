"""
API REST de statebate-pulse (FastAPI).

Endpoints públicos (sin auth, cacheables):
  GET  /api/top-tippers?days=30&limit=50
  GET  /api/top-models?limit=50
  GET  /api/rooms/online
  GET  /api/rooms/{slug}/schedule          (heatmap 7x24)
  GET  /api/rooms/{slug}/boost-report      (recomendaciones accionables)
  GET  /api/rooms/{slug}/match-tippers     (top tippers afines por horario)

Endpoints admin (header X-Api-Key):
  POST /api/admin/refresh-leaderboards
  POST /api/admin/scrape/once
  GET  /api/admin/scrape/status

Pixel:
  GET  /p/track.gif, POST /p/track, GET /p/pixel.js   (ver pixel/tracker.py)
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from algorithms import schedule_matcher, top_tippers, traffic_booster
from config import settings
from db import repository as repo
from db.database import dispose

log = structlog.get_logger()

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    structlog.configure(processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ])
    # Jobs periódicos
    scheduler.add_job(repo.refresh_leaderboards, "interval", minutes=15,
                      id="refresh-leaderboards", replace_existing=True)
    scheduler.add_job(_scrape_cycle, "interval", minutes=15,
                      id="scrape-cycle", replace_existing=True,
                      max_instances=1, coalesce=True)
    scheduler.add_job(_scrape_cycle_deep, "cron", hour=4, minute=15,
                      id="scrape-deep", replace_existing=True)
    scheduler.start()
    log.info("api.start", env=settings.app_env, port=settings.app_port)
    yield
    # --- shutdown ---
    scheduler.shutdown(wait=False)
    await dispose()
    log.info("api.stop")


app = FastAPI(
    title="statebate-pulse API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs" if not settings.is_prod else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.is_prod
    else ["http://localhost:3000", "http://127.0.0.1:3000", "https://*.github.io"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Monta el router del pixel en /p
from pixel.tracker import router as pixel_router  # noqa: E402
app.include_router(pixel_router)


# ---------- helpers ----------

async def _require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = settings.api_key.get_secret_value()
    if not expected:
        # Si no hay API_KEY configurada en dev, permite
        if not settings.is_prod:
            return
        raise HTTPException(503, "API_KEY no configurada")
    if not x_api_key or x_api_key != expected:
        raise HTTPException(401, "api key inválida")


async def _scrape_cycle() -> None:
    from scraper.runner import run_once
    try:
        await run_once(deep_profiles=False)
    except Exception as e:
        log.error("scrape.cycle.fail", error=str(e))


async def _scrape_cycle_deep() -> None:
    from scraper.runner import run_once
    try:
        await run_once(deep_profiles=True)
    except Exception as e:
        log.error("scrape.deep.fail", error=str(e))


# ---------- endpoints públicos ----------

@app.get("/api/healthz")
async def healthz() -> dict:
    return {"ok": True}


@app.get("/api/top-tippers")
async def api_top_tippers(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=500),
):
    """Leaderboard de tippers por score compuesto."""
    ranked = await top_tippers.rank_tippers(days=days, limit=limit)
    return {
        "days": days,
        "count": len(ranked),
        "weights": top_tippers.W,
        "items": [t.__dict__ for t in ranked],
    }


@app.get("/api/top-models")
async def api_top_models(limit: int = Query(50, ge=1, le=500)):
    rows = await repo.top_models(limit=limit)
    return {"count": len(rows), "items": rows}


@app.get("/api/rooms/online")
async def api_rooms_online(limit: int = Query(100, ge=1, le=500)):
    """Salas online ahora (último snapshot)."""
    from sqlalchemy import text
    from db.database import get_session
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT DISTINCT ON (rs.room_slug)
                    rs.room_slug, m.username, m.display_name, m.gender,
                    m.country, m.tags, rs.viewers, rs.session_tokens,
                    rs.top_tipper_session, rs.scraped_at
                FROM room_snapshots rs
                JOIN models m ON m.room_slug = rs.room_slug
                WHERE rs.scraped_at > now() - INTERVAL '20 minutes'
                ORDER BY rs.room_slug, rs.scraped_at DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        cols = res.keys()
        rows = [dict(zip(cols, r)) for r in res.fetchall()]
        # viewer/timestamps a serializable
        for r in rows:
            r["scraped_at"] = r["scraped_at"].isoformat() if r["scraped_at"] else None
        return {"count": len(rows), "items": rows}


@app.get("/api/rooms/{slug}/schedule")
async def api_room_schedule(slug: str):
    rows = await repo.schedule_heatmap(slug)
    return {"room_slug": slug, "slots": rows}


@app.get("/api/rooms/{slug}/boost-report")
async def api_boost_report(slug: str):
    report = await traffic_booster.boost_report(slug)
    # datetime → iso
    report["generated_at"] = report["generated_at"].isoformat() if hasattr(report["generated_at"], "isoformat") else report["generated_at"]
    return report


@app.get("/api/rooms/{slug}/match-tippers")
async def api_match_tippers(slug: str, limit: int = Query(20, ge=1, le=100)):
    """Tippers cuyo perfil de actividad mejor se alinea con los horarios de la modelo."""
    items = await schedule_matcher.best_tippers_for_model(slug, limit=limit)
    return {"room_slug": slug, "count": len(items), "items": items}


@app.get("/api/rooms/{slug}/top-tippers-direct")
async def api_top_tippers_for_model(slug: str, limit: int = Query(20, ge=1, le=100)):
    """Top tippers combinando historial directo + afinidad horaria."""
    items = await top_tippers.top_tippers_for_model(slug, limit=limit)
    return {"room_slug": slug, "count": len(items), "items": items}


@app.get("/api/traffic/leaderboard")
async def api_traffic_leaderboard(limit: int = Query(50, ge=1, le=200)):
    scores = await traffic_booster.traffic_leaderboard(limit=limit)
    return {"count": len(scores), "items": [s.__dict__ for s in scores]}


# ---------- endpoints admin ----------

@app.post("/api/admin/refresh-leaderboards", dependencies=[Depends(_require_api_key)])
async def admin_refresh():
    await repo.refresh_leaderboards()
    return {"ok": True}


@app.post("/api/admin/scrape/once", dependencies=[Depends(_require_api_key)])
async def admin_scrape_once(deep: bool = False):
    from scraper.runner import run_once
    summary = await run_once(deep_profiles=deep)
    return summary


@app.get("/api/admin/scrape/jobs", dependencies=[Depends(_require_api_key)])
async def admin_scrape_jobs():
    jobs = []
    for j in scheduler.get_jobs():
        jobs.append({
            "id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            "trigger": str(j.trigger),
        })
    return {"jobs": jobs}
