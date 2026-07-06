"""
Orquestador del scraping periódico.

Pipeline por ciclo (cada N minutos):
  1. scrape_all_online()  → listado de salas online ahora
  2. Para cada sala online:
     a. scrape_room()     → snapshot + recent_tips
     b. (opcional) scrape_profile()  → bio + horario declarado (1x/día)
     c. registrar ScheduleObservation (online ahora, viewers X)
  3. Persistir todo en DB
  4. Recalcular agregados (top tippers, schedule heatmaps) — ver algorithms/

Se puede correr:
  - Manual:        python -m scraper.runner once
  - Loop continuo: python -m scraper.runner loop --interval 900
  - Como worker dentro del proceso API (APScheduler)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone

from scraper import graph_profile, graph_room, graph_search

log = logging.getLogger(__name__)


async def run_once(*, deep_profiles: bool = False) -> dict:
    """Una corrida completa. Devuelve un resumen métrico."""
    from db.repository import (
        upsert_model,
        upsert_room_snapshot,
        insert_tip_event,
        insert_schedule_observation,
        models_needing_profile,
    )

    started = datetime.now(timezone.utc)
    log.info("=== ciclo de scraping iniciado ===")

    # 1. Listado online
    rooms = await graph_search.scrape_all_online(max_pages=10)
    log.info("salas online: %d", len(rooms))

    n_snapshots = 0
    n_tips = 0
    n_profiles = 0
    n_obs = 0

    if rooms:
        # 2. Detalle de cada sala (batched por graph_room.scrape_rooms)
        slugs = [r.room_slug for r in rooms]
        details = await graph_room.scrape_rooms(slugs)

        now = datetime.now(timezone.utc)
        for item in rooms:
            # Upsert modelo + snapshot + observación de schedule
            await upsert_model(item)
            await insert_schedule_observation(item, observed_at=now)
            n_obs += 1

        for d in details:
            await upsert_room_snapshot(d)
            n_snapshots += 1
            for t in d.recent_tips:
                t.room_slug = d.room_slug
                await insert_tip_event(t)
                n_tips += 1

        # 3. Perfiles profundos (solo para modelos que no tienen perfil fresco)
        if deep_profiles:
            stale = await models_needing_profile(days=1, limit=50)
            if stale:
                profs = await graph_profile.scrape_profiles(stale)
                for p in profs:
                    await upsert_model(p)  # el repo mergea campos de perfil
                    n_profiles += 1

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    summary = {
        "started_at": started.isoformat(),
        "elapsed_s": round(elapsed, 1),
        "rooms_online": len(rooms),
        "snapshots": n_snapshots,
        "tips_ingested": n_tips,
        "profiles_refreshed": n_profiles,
        "schedule_observations": n_obs,
    }
    log.info("=== ciclo terminado: %s ===", summary)
    return summary


async def run_loop(interval_s: int = 900, deep_every: int = 24) -> None:
    """Loop infinito con descanso. deep_profiles cada `deep_every` horas."""
    cycle = 0
    while True:
        try:
            deep = (cycle % max(1, deep_every)) == 0
            await run_once(deep_profiles=deep)
        except Exception as e:
            log.exception("ciclo falló: %s", e)
        cycle += 1
        await asyncio.sleep(interval_s)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_once = sub.add_parser("once")
    p_once.add_argument("--deep", action="store_true")
    p_loop = sub.add_parser("loop")
    p_loop.add_argument("--interval", type=int, default=900)
    p_loop.add_argument("--deep-every", type=int, default=24)
    args = ap.parse_args()

    if args.cmd == "once":
        asyncio.run(run_once(deep_profiles=args.deep))
    else:
        asyncio.run(run_loop(interval_s=args.interval, deep_every=args.deep_every))


if __name__ == "__main__":
    main()
