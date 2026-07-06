"""
Traffic Booster — el "pixel de meta" de statebate-pulse.

Concepto:
  Meta Pixel = un JS beacon que pones en TU sitio para medir conversiones y
  alimentar el algoritmo de optimización de anuncios.

  Aquí no controlamos el cam site, así que el "pixel" opera en dos modos:

  1. MODO BEACON (analytics de tráfico propio):
     Un endpoint /p/track que sirve un GIF 1x1 transparente. Modelos/afiliados
     que QUIEREN tráfico lo embeben en SUS propias páginas promo (Twitter bio,
     Linktree, sitio personal). Cada load = un "view" atribuido a su room_slug.
     Mide embudo: view → click → land en sala → tip (conversión).

  2. MODO RECOMMENDER (boost orgánico):
     A partir de los datos scrapeados + pixel events, calcula:
       - "traffic_score" por sala (viewers × tiempo × crecimiento)
       - horario óptimo de transmisión (ver schedule_matcher.best_time_to_go_live)
       - cross-promotion: qué modelos deberían shoutoutearse entre sí por
         audiencia complementaria (baja solapamiento de tippers pero mismo tag)

  El "boost" de tráfico se logra así:
     a. Recomendaciones accionables a la modelo (mejor horario, mejor partne de cross-promo).
     b. Generación de enlaces de afiliado (si el sitio tiene programa) para que
        modelos/afiliados compartan con tracking atribuido.
     c. (Opcional) Feed RSS/JSON público del dashboard para SEO en GitHub Pages:
        indexar "top modelos online ahora" genera tráfico orgánico de búsqueda.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from db.database import get_session
from algorithms.schedule_matcher import best_time_to_go_live

log = logging.getLogger(__name__)


@dataclass
class TrafficScore:
    room_slug: str
    score: float
    viewers_now: int
    viewers_growth_1h: float
    tokens_velocity_1h: float
    rank: int | None = None


async def traffic_score(room_slug: str) -> TrafficScore | None:
    """Score 0-100 de "salud de tráfico" de una sala ahora mismo."""
    async with get_session() as s:
        res = await s.execute(
            text("""
                WITH now_v AS (
                    SELECT viewers, scraped_at
                    FROM room_snapshots
                    WHERE room_slug = :slug
                    ORDER BY scraped_at DESC
                    LIMIT 1
                ),
                past_1h AS (
                    SELECT AVG(viewers)::float AS avg_v
                    FROM room_snapshots
                    WHERE room_slug = :slug
                      AND scraped_at > now() - INTERVAL '1 hour'
                ),
                tips_1h AS (
                    SELECT COALESCE(SUM(amount),0)::float AS t
                    FROM tip_events
                    WHERE room_slug = :slug
                      AND occurred_at > now() - INTERVAL '1 hour'
                )
                SELECT
                    n.viewers,
                    p.avg_v,
                    ti.t,
                    n.scraped_at
                FROM now_v n, past_1h p, tips_1h ti
            """),
            {"slug": room_slug},
        )
        row = res.fetchone()
        if not row:
            return None
        viewers_now, avg_1h, tokens_1h, _ = row
        viewers_now = int(viewers_now or 0)
        avg_1h = float(avg_1h or 0)
        tokens_1h = float(tokens_1h or 0)
        growth = ((viewers_now - avg_1h) / avg_1h) if avg_1h > 0 else 0.0

        # Score compuesto normalizado
        v_part = min(viewers_now / 500.0, 1.0) * 50      # viewers: hasta 50 pts
        g_part = max(0.0, min(growth, 1.0)) * 25          # crecimiento: hasta 25
        t_part = min(tokens_1h / 5000.0, 1.0) * 25        # velocity: hasta 25
        score = round(v_part + g_part + t_part, 2)

        return TrafficScore(
            room_slug=room_slug,
            score=score,
            viewers_now=viewers_now,
            viewers_growth_1h=round(growth, 4),
            tokens_velocity_1h=tokens_1h,
        )


async def traffic_leaderboard(limit: int = 50) -> list[TrafficScore]:
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT DISTINCT room_slug
                FROM room_snapshots
                WHERE scraped_at > now() - INTERVAL '15 minutes'
                ORDER BY room_slug
            """),
        )
        slugs = [r[0] for r in res.fetchall()]

    scores: list[TrafficScore] = []
    for slug in slugs:
        ts = await traffic_score(slug)
        if ts:
            scores.append(ts)
    scores.sort(key=lambda x: x.score, reverse=True)
    for i, sc in enumerate(scores, 1):
        sc.rank = i
        if i > limit:
            break
    return scores[:limit]


async def cross_promo_candidates(room_slug: str, limit: int = 5) -> list[dict]:
    """Modelos con audiencia COMPLEMENTARIA (pocos tippers compartidos)
    pero mismo tag → buenas candidatas para cross-promo / shoutout mutuo.

    Lógica: para cada otra modelo con mismo tag, calcula el solapamiento de
    tippers (intersección / unión). Prioriza las de menor solapamiento (audiencias
    distintas) pero con volumen similar → máxima ganancia de exposición mutua.
    """
    async with get_session() as s:
        res = await s.execute(
            text("""
                WITH my_tags AS (
                    SELECT unnest(tags) AS tag FROM models WHERE room_slug = :slug
                ),
                my_tippers AS (
                    SELECT DISTINCT tipper_username
                    FROM tip_events
                    WHERE room_slug = :slug
                      AND occurred_at > now() - INTERVAL '30 days'
                ),
                candidates AS (
                    SELECT m.room_slug, m.display_name, m.tags
                    FROM models m
                    WHERE m.room_slug <> :slug
                      AND m.tags && (SELECT array_agg(tag) FROM my_tags)
                )
                SELECT c.room_slug, c.display_name, c.tags,
                       (SELECT COUNT(DISTINCT te.tipper_username)
                        FROM tip_events te
                        WHERE te.room_slug = c.room_slug
                          AND te.tipper_username IN (SELECT tipper_username FROM my_tippers)
                       ) AS shared_tippers,
                       (SELECT COUNT(DISTINCT tipper_username)
                        FROM tip_events te
                        WHERE te.room_slug = c.room_slug
                          AND te.occurred_at > now() - INTERVAL '30 days'
                       ) AS their_tippers
                FROM candidates c
                ORDER BY shared_tippers ASC, their_tippers DESC
                LIMIT :limit
            """),
            {"slug": room_slug, "limit": limit},
        )
        cols = res.keys()
        return [dict(zip(cols, row)) for row in res.fetchall()]


async def boost_report(room_slug: str) -> dict:
    """Reporte accionable para una modelo que quiere aumentar su tráfico."""
    score = await traffic_score(room_slug)
    best_slot = await best_time_to_go_live(room_slug)
    cross = await cross_promo_candidates(room_slug, limit=5)
    return {
        "room_slug": room_slug,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "traffic_score": score.__dict__ if score else None,
        "recommended_go_live_slot": best_slot,
        "cross_promo_candidates": cross,
        "affiliate_link": _affiliate_link(room_slug),
    }


def _affiliate_link(room_slug: str) -> str | None:
    """Genera un enlace de afiliado si BOOST_AFFILIATE_ID está configurado."""
    from config import settings
    if not settings.boost_affiliate_id:
        return None
    return f"{settings.target_base_url}/{room_slug}?ref={settings.boost_affiliate_id}"
