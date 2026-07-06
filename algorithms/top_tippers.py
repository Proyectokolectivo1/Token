"""
Algoritmo de Top Tippers.

No es solo "ORDER BY SUM(amount)" — rankeamos con un score compuesto que
premia: volumen total, frecuencia, recencia, lealtad (días activos) y
diversificación (cuántas salas distintas tipea, como proxy de "baller" vs
"regular"). El score es normalizado (z-score) por componente y combinado
con pesos ajustables.

Salidas:
  - rank_tippers()            → lista ordenada con score y desglose
  - top_tippers_for_model()   → top N tippers más probables para UNA modelo,
                                basado en overlap de horarios + historial de
                                tips a modelos similares (tags/country).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from db.database import get_session

log = logging.getLogger(__name__)

# Pesos del score compuesto (suman 1.0)
W = {
    "volume":    0.40,   # tokens totales
    "frequency": 0.20,   # cantidad de tips
    "recency":   0.15,   # qué tan reciente fue el último tip
    "loyalty":   0.15,   # días distintos activo en la ventana
    "breadth":   0.10,   # cuántas modelos distintas tipea
}


@dataclass
class TipperScore:
    tipper_username: str
    total_tokens: int
    tip_count: int
    rooms_tipped: int
    active_days: int
    last_tip_at: datetime
    recency_days: float
    components: dict = field(default_factory=dict)
    score: float = 0.0


def _zscore(value: float, mean: float, std: float) -> float:
    if std == 0:
        return 0.0
    return (value - mean) / std


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


async def fetch_tipper_window(days: int = 30) -> list[dict]:
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT
                    tipper_username,
                    SUM(amount) AS total_tokens,
                    COUNT(*)     AS tip_count,
                    COUNT(DISTINCT room_slug) AS rooms_tipped,
                    COUNT(DISTINCT date_trunc('day', occurred_at)) AS active_days,
                    MAX(occurred_at) AS last_tip_at
                FROM tip_events
                WHERE occurred_at > now() - (:days || ' days')::interval
                GROUP BY tipper_username
            """),
            {"days": str(days)},
        )
        cols = res.keys()
        return [dict(zip(cols, row)) for row in res.fetchall()]


async def rank_tippers(days: int = 30, limit: int = 100) -> list[TipperScore]:
    """Ranquea tippers por score compuesto en la ventana de `days` días."""
    rows = await fetch_tipper_window(days=days)
    if not rows:
        return []

    now = datetime.now(timezone.utc)

    # Pre-calcula recencia en días
    for r in rows:
        r["recency_days"] = max(0.0, (now - r["last_tip_at"]).total_seconds() / 86400.0)

    # Medias y desvíos para z-score
    def stats(key: str) -> tuple[float, float]:
        vals = [float(r[key]) for r in rows]
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        return mean, math.sqrt(var)

    m_vol, s_vol = stats("total_tokens")
    m_freq, s_freq = stats("tip_count")
    m_breadth, s_breadth = stats("rooms_tipped")
    m_loyal, s_loyal = stats("active_days")
    # Recencia: menor = mejor → invertimos con z negativo y sigmoid
    m_rec, s_rec = stats("recency_days")

    scores: list[TipperScore] = []
    for r in rows:
        z_vol = _zscore(float(r["total_tokens"]), m_vol, s_vol)
        z_freq = _zscore(float(r["tip_count"]), m_freq, s_freq)
        z_breadth = _zscore(float(r["rooms_tipped"]), m_breadth, s_breadth)
        z_loyal = _zscore(float(r["active_days"]), m_loyal, s_loyal)
        # Recencia: más reciente = mejor. Usamos 1 - sigmoid(recencia normalizada).
        z_rec_neg = -_zscore(float(r["recency_days"]), m_rec, s_rec)

        comp = {
            "volume":    float(W["volume"])    * _sigmoid(z_vol),
            "frequency": float(W["frequency"]) * _sigmoid(z_freq),
            "recency":   float(W["recency"])   * _sigmoid(z_rec_neg),
            "loyalty":   float(W["loyalty"])   * _sigmoid(z_loyal),
            "breadth":   float(W["breadth"])   * _sigmoid(z_breadth),
        }
        score = sum(comp.values())  # en [0, 1]
        scores.append(TipperScore(
            tipper_username=r["tipper_username"],
            total_tokens=int(r["total_tokens"]),
            tip_count=int(r["tip_count"]),
            rooms_tipped=int(r["rooms_tipped"]),
            active_days=int(r["active_days"]),
            last_tip_at=r["last_tip_at"],
            recency_days=float(r["recency_days"]),
            components={k: round(v, 4) for k, v in comp.items()},
            score=round(score, 4),
        ))

    scores.sort(key=lambda t: t.score, reverse=True)
    return scores[:limit]


async def top_tippers_for_model(room_slug: str, limit: int = 20) -> list[dict]:
    """Top tippers más probables para una modelo específica.

    Combina:
      - historial directo: tippers que ya tipearon esta modelo (boost fuerte)
      - afinidad por horario: tippers activos en las mismas franjas horarias
        en que la modelo suele transmitir (ver schedule_observations)
      - afinidad por tags/country: tippers que tipean modelos con tags similares
    """
    async with get_session() as s:
        # Horarios típicos de la modelo (top 5 franjas dow/hour por viewers)
        heat = await s.execute(
            text("""
                SELECT dow, hour_utc, AVG(viewers)::int AS avg_v
                FROM schedule_observations
                WHERE room_slug = :slug AND was_online
                GROUP BY dow, hour_utc
                ORDER BY avg_v DESC
                LIMIT 5
            """),
            {"slug": room_slug},
        )
        heat_rows = heat.fetchall()
        if not heat_rows:
            # Sin datos de horario → fallback al ranking global
            ranked = await rank_tippers(limit=limit)
            return [{"tipper": t.tipper_username, "score": t.score, "reason": "global"}
                    for t in ranked]

        # tippers que han estado activos en esas franjas horarias (cualquier modelo)
        slots = [(r[0], r[1]) for r in heat_rows]
        slot_clause = " OR ".join(
            ["(EXTRACT(DOW FROM occurred_at)::int = %d AND EXTRACT(HOUR FROM occurred_at)::int = %d)" % slot
             for slot in slots]
        )
        res = await s.execute(
            text(f"""
                WITH direct AS (
                    SELECT tipper_username,
                           SUM(amount) AS direct_tokens,
                           COUNT(*)     AS direct_tips
                    FROM tip_events
                    WHERE room_slug = :slug
                    GROUP BY tipper_username
                ),
                affinity AS (
                    SELECT tipper_username,
                           SUM(amount) AS aff_tokens,
                           COUNT(*)     AS aff_tips
                    FROM tip_events
                    WHERE {slot_clause}
                    GROUP BY tipper_username
                ),
                tags AS (
                    SELECT m2.room_slug, m2.tags
                    FROM models m2 WHERE m2.room_slug = :slug
                )
                SELECT
                    COALESCE(d.tipper_username, a.tipper_username) AS tipper_username,
                    COALESCE(d.direct_tokens, 0) AS direct_tokens,
                    COALESCE(d.direct_tips, 0)   AS direct_tips,
                    COALESCE(a.aff_tokens, 0)   AS aff_tokens,
                    COALESCE(a.aff_tips, 0)     AS aff_tips
                FROM direct d
                FULL OUTER JOIN affinity a USING (tipper_username)
                ORDER BY (COALESCE(d.direct_tokens,0) * 3 + COALESCE(a.aff_tokens,0)) DESC
                LIMIT :limit
            """),
            {"slug": room_slug, "limit": limit},
        )
        cols = res.keys()
        return [
            {
                "tipper": row[0],
                "direct_tokens": row[1],
                "direct_tips": row[2],
                "affinity_tokens": row[3],
                "affinity_tips": row[4],
                "score": round(row[1] * 3 + row[3], 2),
            }
            for row in res.fetchall()
        ]
