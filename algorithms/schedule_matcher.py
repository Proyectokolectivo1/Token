"""
Schedule Matcher: empareja modelos y tippers por overlapping de horarios.

Cada modelo tiene un "perfil horario" (distribución 7x24 de probabilidad de
estar online, derivada de schedule_observations). Cada tipper tiene un
"perfil de actividad" (distribución 7x24 de cuándo tipea). El match score
es la similitud coseno entre ambos vectores.

Usos:
  - Para una modelo: qué tippers tienen el perfil de actividad más alineado
    con sus horarios (candidatos a "top tipper" en su próxima transmisión).
  - Para un tipper: qué modelos están online en sus horas pico.
  - "Best time to go live": franja horaria con mayor concentración de
    tippers activos que aún no tipean a esta modelo (oportunidad).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from sqlalchemy import text

from db.database import get_session

log = logging.getLogger(__name__)

# 7 días x 24 horas = 168 dimensiones
SLOTS = 168


def _slot_idx(dow: int, hour: int) -> int:
    return dow * 24 + hour


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class Profile:
    key: str
    vector: list[float]  # len 168, normalizado


async def model_schedule_vector(room_slug: str) -> Profile:
    """Distribución de actividad online de la modelo (basada en observaciones)."""
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT dow, hour_utc, AVG(viewers)::float AS v
                FROM schedule_observations
                WHERE room_slug = :slug AND was_online
                GROUP BY dow, hour_utc
            """),
            {"slug": room_slug},
        )
        vec = [0.0] * SLOTS
        for dow, hour, v in res.fetchall():
            vec[_slot_idx(int(dow), int(hour))] = float(v)
        return Profile(key=room_slug, vector=vec)


async def tipper_activity_vector(tipper_username: str, days: int = 30) -> Profile:
    """Distribución de cuándo tipea un tipper (frecuencia por slot)."""
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT EXTRACT(DOW FROM occurred_at)::int AS dow,
                       EXTRACT(HOUR FROM occurred_at)::int AS hour,
                       COUNT(*)::float AS c
                FROM tip_events
                WHERE tipper_username = :u
                  AND occurred_at > now() - (:days || ' days')::interval
                GROUP BY dow, hour
            """),
            {"u": tipper_username, "days": str(days)},
        )
        vec = [0.0] * SLOTS
        for dow, hour, c in res.fetchall():
            vec[_slot_idx(int(dow), int(hour))] = float(c)
        return Profile(key=tipper_username, vector=vec)


async def match_tipper_to_model(room_slug: str, tipper_username: str) -> float:
    mp = await model_schedule_vector(room_slug)
    tp = await tipper_activity_vector(tipper_username)
    return _cosine(mp.vector, tp.vector)


async def best_tippers_for_model(room_slug: str, limit: int = 20) -> list[dict]:
    """Tippers cuyo perfil de actividad mejor se alinea con los horarios de la modelo."""
    mp = await model_schedule_vector(room_slug)
    if sum(mp.vector) == 0:
        return []
    async with get_session() as s:
        # Solo tippers con historial reciente para no escanear todo
        res = await s.execute(
            text("""
                SELECT DISTINCT tipper_username
                FROM tip_events
                WHERE occurred_at > now() - INTERVAL '30 days'
                LIMIT 2000
            """),
        )
        tippers = [r[0] for r in res.fetchall()]

    scored: list[dict] = []
    for t in tippers:
        tp = await tipper_activity_vector(t)
        if sum(tp.vector) == 0:
            continue
        sim = _cosine(mp.vector, tp.vector)
        scored.append({"tipper": t, "similarity": round(sim, 4)})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:limit]


async def best_time_to_go_live(room_slug: str) -> dict | None:
    """Recomienda el slot (dow, hour) con más tippers activos que NO suelen
    ver a esta modelo → mayor oportunidad de captar tráfico/tips nuevos."""
    mp = await model_schedule_vector(room_slug)
    async with get_session() as s:
        # Actividad global de tippers por slot
        res = await s.execute(
            text("""
                SELECT EXTRACT(DOW FROM occurred_at)::int AS dow,
                       EXTRACT(HOUR FROM occurred_at)::int AS hour,
                       COUNT(DISTINCT tipper_username) AS n_tippers,
                       SUM(amount) AS tokens
                FROM tip_events
                WHERE occurred_at > now() - INTERVAL '30 days'
                GROUP BY dow, hour
            """),
        )
        global_slots = {(int(r[0]), int(r[1])): {"tippers": r[2], "tokens": r[3]}
                        for r in res.fetchall()}

    if not global_slots:
        return None

    # Score por slot = (tipper_density global) * (1 - modelo ya online)
    best = None
    for (dow, hour), info in global_slots.items():
        model_online = mp.vector[_slot_idx(dow, hour)] > 0
        # Penaliza si la modelo ya transmite ahí (no es "nueva" oportunidad)
        opp = info["tippers"] * (0.3 if model_online else 1.0)
        if best is None or opp > best["opportunity"]:
            best = {
                "dow": dow, "hour_utc": hour,
                "active_tippers": info["tippers"],
                "tokens_in_slot": info["tokens"],
                "model_usually_online": model_online,
                "opportunity": opp,
            }
    return best
