"""
Repository: funciones de persistencia para modelos, snapshots, tips,
observaciones de schedule y eventos del pixel.

Usa SQL crudo (text()) para máximo control y performance; los modelos Pydantic
viven en scraper.models y aquí solo los convertimos a dict para insertar.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from db.database import get_session
from scraper.models import (
    ProfileDetail,
    RoomDetail,
    RoomListItem,
    ScheduleObservation,
    TipEvent,
)

log = logging.getLogger(__name__)


def _hash_tipper(username: str) -> str:
    """Hash determinista (sin salt externo) solo para deduplicar/anonimizar
    en reportes. NO es PII-safe real; si necesitas privacidad fuerte usa HMAC
    con clave server-side."""
    return hashlib.sha256(username.encode()).hexdigest()[:16]


# ============ MODELOS / PERFORMERS ============

async def upsert_model(item: RoomListItem | ProfileDetail) -> None:
    """Insert or update de una modelo. Acepta listing o profile."""
    async with get_session() as s:
        if isinstance(item, ProfileDetail):
            await s.execute(
                text("""
                    INSERT INTO models
                        (room_slug, username, display_name, gender, age, country,
                         bio, followers, total_views, declared_schedule, avatar_url,
                         tags, profile_fetched_at, updated_at)
                    VALUES
                        (:room_slug, :username, :display_name, :gender, :age, :country,
                         :bio, :followers, :total_views, :declared_schedule, :avatar_url,
                         :tags, now(), now())
                    ON CONFLICT (room_slug) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, models.display_name),
                        gender       = COALESCE(EXCLUDED.gender, models.gender),
                        age          = COALESCE(EXCLUDED.age, models.age),
                        country      = COALESCE(EXCLUDED.country, models.country),
                        bio          = COALESCE(EXCLUDED.bio, models.bio),
                        followers    = COALESCE(EXCLUDED.followers, models.followers),
                        total_views  = COALESCE(EXCLUDED.total_views, models.total_views),
                        declared_schedule = COALESCE(EXCLUDED.declared_schedule, models.declared_schedule),
                        avatar_url   = COALESCE(EXCLUDED.avatar_url, models.avatar_url),
                        tags         = CASE WHEN array_length(EXCLUDED.tags,1) > 0
                                            THEN EXCLUDED.tags ELSE models.tags END,
                        profile_fetched_at = now(),
                        updated_at   = now()
                """),
                {
                    "room_slug": item.room_slug,
                    "username": item.username,
                    "display_name": item.display_name,
                    "gender": item.gender.value if item.gender else None,
                    "age": item.age,
                    "country": item.country,
                    "bio": (item.bio or "")[:2000] if item.bio else None,
                    "followers": item.followers,
                    "total_views": item.total_views,
                    "declared_schedule": item.declared_schedule,
                    "avatar_url": str(item.avatar_url) if item.avatar_url else None,
                    "tags": item.tags,
                },
            )
        else:  # RoomListItem
            await s.execute(
                text("""
                    INSERT INTO models
                        (room_slug, username, display_name, gender, age, country,
                         followers, tags, updated_at, first_seen_at)
                    VALUES
                        (:room_slug, :username, :display_name, :gender, :age, :country,
                         0, :tags, now(), now())
                    ON CONFLICT (room_slug) DO UPDATE SET
                        display_name = COALESCE(EXCLUDED.display_name, models.display_name),
                        gender       = COALESCE(EXCLUDED.gender, models.gender),
                        age          = COALESCE(EXCLUDED.age, models.age),
                        country      = COALESCE(EXCLUDED.country, models.country),
                        tags         = CASE WHEN array_length(EXCLUDED.tags,1) > 0
                                            THEN EXCLUDED.tags ELSE models.tags END,
                        updated_at   = now()
                """),
                {
                    "room_slug": item.room_slug,
                    "username": item.username,
                    "display_name": item.display_name,
                    "gender": item.gender.value if item.gender else None,
                    "age": item.age,
                    "country": item.country,
                    "tags": item.tags,
                },
            )


async def models_needing_profile(days: int = 1, limit: int = 50) -> list[str]:
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT room_slug FROM models
                WHERE profile_fetched_at IS NULL
                   OR profile_fetched_at < now() - (:days || ' days')::interval
                ORDER BY updated_at DESC
                LIMIT :limit
            """),
            {"days": str(days), "limit": limit},
        )
        return [r[0] for r in res.fetchall()]


# ============ SNAPSHOTS ============

async def upsert_room_snapshot(d: RoomDetail) -> None:
    async with get_session() as s:
        await s.execute(
            text("""
                INSERT INTO room_snapshots
                    (room_slug, viewers, followers, room_status, session_started_at,
                     session_tokens, top_tipper_session, scraped_at)
                VALUES
                    (:room_slug, :viewers, :followers, :room_status, :session_started_at,
                     :session_tokens, :top_tipper_session, now())
            """),
            {
                "room_slug": d.room_slug,
                "viewers": d.viewers,
                "followers": d.followers,
                "room_status": d.room_status.value,
                "session_started_at": d.session_started_at,
                "session_tokens": d.session_tokens,
                "top_tipper_session": d.top_tipper_session,
            },
        )


# ============ TIPS ============

async def insert_tip_event(t: TipEvent) -> bool:
    """Inserta un tip; devuelve True si fue nuevo (dedupe por UNIQUE)."""
    if not t.tipper_hash:
        t.tipper_hash = _hash_tipper(t.tipper_username)
    async with get_session() as s:
        try:
            await s.execute(
                text("""
                    INSERT INTO tip_events
                        (room_slug, tipper_username, tipper_hash, amount, currency,
                         message, occurred_at, ingested_at)
                    VALUES
                        (:room_slug, :tipper, :hash, :amount, :currency,
                         :message, :occurred_at, now())
                    ON CONFLICT (room_slug, tipper_username, occurred_at, amount)
                    DO NOTHING
                """),
                {
                    "room_slug": t.room_slug,
                    "tipper": t.tipper_username,
                    "hash": t.tipper_hash,
                    "amount": t.amount,
                    "currency": t.currency,
                    "message": t.message,
                    "occurred_at": t.occurred_at,
                },
            )
            return True
        except Exception as e:
            log.error("insert tip falló: %s", e)
            return False


# ============ SCHEDULE OBSERVATIONS ============

async def insert_schedule_observation(
    item: RoomListItem, observed_at: datetime | None = None
) -> None:
    ts = observed_at or datetime.now(timezone.utc)
    async with get_session() as s:
        await s.execute(
            text("""
                INSERT INTO schedule_observations
                    (room_slug, observed_at, was_online, viewers, dow, hour_utc)
                VALUES
                    (:room_slug, :observed_at, TRUE, :viewers,
                     EXTRACT(DOW FROM :observed_at)::int,
                     EXTRACT(HOUR FROM :observed_at)::int)
            """),
            {
                "room_slug": item.room_slug,
                "observed_at": ts,
                "viewers": item.viewers,
            },
        )


# ============ PIXEL EVENTS ============

async def insert_pixel_event(
    *, room_slug: str | None, event: str, client_ip: str | None,
    user_agent: str | None, referrer: str | None, viewer_id: str | None,
    payload: dict, affiliate_id: str | None = None,
) -> None:
    async with get_session() as s:
        await s.execute(
            text("""
                INSERT INTO pixel_events
                    (room_slug, event, client_ip, user_agent, referrer,
                     viewer_id, payload, affiliate_id)
                VALUES
                    (:room_slug, :event, :client_ip::inet, :ua, :ref,
                     :viewer_id, :payload::jsonb, :aff)
            """),
            {
                "room_slug": room_slug,
                "event": event,
                "client_ip": client_ip,
                "ua": user_agent,
                "ref": referrer,
                "viewer_id": viewer_id,
                "payload": __import__("json").dumps(payload),
                "aff": affiliate_id,
            },
        )


# ============ AGREGADOS (refresh) ============

async def refresh_leaderboards() -> None:
    async with get_session() as s:
        await s.execute(text("SELECT refresh_leaderboards();"))


async def top_tippers(limit: int = 100, days: int = 30) -> list[dict]:
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT * FROM mv_tipper_leaderboard
                ORDER BY total_tokens DESC
                LIMIT :limit
            """),
            {"limit": limit},
        )
        cols = res.keys()
        return [dict(zip(cols, row)) for row in res.fetchall()]


async def top_models(limit: int = 100) -> list[dict]:
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT * FROM mv_model_earnings
                ORDER BY total_tokens_30d DESC NULLS LAST
                LIMIT :limit
            """),
            {"limit": limit},
        )
        cols = res.keys()
        return [dict(zip(cols, row)) for row in res.fetchall()]


async def schedule_heatmap(room_slug: str) -> list[dict]:
    """Devuelve 7x24 = 168 filas con viewers promedio por (dow, hour)."""
    async with get_session() as s:
        res = await s.execute(
            text("""
                SELECT dow, hour_utc,
                       AVG(viewers)::numeric(10,1) AS avg_viewers,
                       COUNT(*)                    AS samples,
                       BOOL_OR(was_online)         AS ever_online
                FROM schedule_observations
                WHERE room_slug = :slug
                GROUP BY dow, hour_utc
                ORDER BY dow, hour_utc
            """),
            {"slug": room_slug},
        )
        cols = res.keys()
        return [dict(zip(cols, row)) for row in res.fetchall()]
