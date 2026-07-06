-- ============================================================
-- statebate-pulse :: schema PostgreSQL 16 + TimescaleDB
-- ============================================================
-- Ejecutar una sola vez. Ver scripts/init_db.py

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- búsqueda fuzzy de slugs
-- TimescaleDB (instalar en la VM): CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ===== Modelos / performers =====
CREATE TABLE IF NOT EXISTS models (
    room_slug     TEXT PRIMARY KEY,
    username      TEXT NOT NULL,
    display_name  TEXT,
    gender        TEXT CHECK (gender IN ('female','male','couple','trans')),
    age           INT  CHECK (age IS NULL OR (age >= 18 AND age <= 99)),
    country       TEXT,
    bio           TEXT,
    followers     BIGINT DEFAULT 0,
    total_views   BIGINT,
    declared_schedule TEXT,                    -- texto libre del perfil
    avatar_url    TEXT,
    tags          TEXT[] DEFAULT '{}',
    profile_fetched_at TIMESTAMPTZ,
    first_seen_at TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_models_username ON models (username);
CREATE INDEX IF NOT EXISTS idx_models_country  ON models (country);
CREATE INDEX IF NOT EXISTS idx_models_tags     ON models USING GIN (tags);

-- ===== Snapshots de sala (una fila por scrapeo) =====
CREATE TABLE IF NOT EXISTS room_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    room_slug       TEXT NOT NULL REFERENCES models(room_slug) ON DELETE CASCADE,
    viewers         INT  NOT NULL DEFAULT 0,
    followers       BIGINT,
    room_status     TEXT NOT NULL DEFAULT 'online',
    session_started_at TIMESTAMPTZ,
    session_tokens  INT  NOT NULL DEFAULT 0,
    top_tipper_session TEXT,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_snap_slug_time ON room_snapshots (room_slug, scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_snap_time      ON room_snapshots (scraped_at DESC);

-- Hipertabla (TimescaleDB) si la extensión está disponible.
-- SELECT create_hypertable('room_snapshots','scraped_at', if_not_exists => TRUE);

-- ===== Tips (evento individual) =====
CREATE TABLE IF NOT EXISTS tip_events (
    id              BIGSERIAL PRIMARY KEY,
    room_slug       TEXT NOT NULL REFERENCES models(room_slug) ON DELETE CASCADE,
    tipper_username TEXT NOT NULL,
    tipper_hash     TEXT,                      -- sha256(username+salt) opcional
    amount          INT  NOT NULL CHECK (amount > 0),
    currency        TEXT NOT NULL DEFAULT 'tokens',
    message         TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (room_slug, tipper_username, occurred_at, amount)
);
CREATE INDEX IF NOT EXISTS idx_tip_room_time  ON tip_events (room_slug, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tip_tipper     ON tip_events (tipper_username);
CREATE INDEX IF NOT EXISTS idx_tip_time       ON tip_events (occurred_at DESC);
-- SELECT create_hypertable('tip_events','occurred_at', if_not_exists => TRUE);

-- ===== Observaciones de schedule (online/offline muestreado) =====
CREATE TABLE IF NOT EXISTS schedule_observations (
    id              BIGSERIAL PRIMARY KEY,
    room_slug       TEXT NOT NULL REFERENCES models(room_slug) ON DELETE CASCADE,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    was_online      BOOLEAN NOT NULL,
    viewers         INT NOT NULL DEFAULT 0,
    dow             SMALLINT NOT NULL CHECK (dow BETWEEN 0 AND 6),  -- 0=Lun
    hour_utc        SMALLINT NOT NULL CHECK (hour_utc BETWEEN 0 AND 23)
);
CREATE INDEX IF NOT EXISTS idx_obs_slug_dow_hour ON schedule_observations (room_slug, dow, hour_utc);
CREATE INDEX IF NOT EXISTS idx_obs_time          ON schedule_observations (observed_at DESC);
-- SELECT create_hypertable('schedule_observations','observed_at', if_not_exists => TRUE);

-- ===== Eventos del pixel (analytics de tráfico) =====
CREATE TABLE IF NOT EXISTS pixel_events (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    room_slug       TEXT,                       -- puede ser nulo si es evento genérico
    event           TEXT NOT NULL,              -- view|click|tip|conversion|share
    client_ip       INET,
    user_agent      TEXT,
    referrer        TEXT,
    viewer_id       TEXT,                       -- cookie anon persistente
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    affiliate_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_pix_room_time ON pixel_events (room_slug, received_at DESC);
CREATE INDEX IF NOT EXISTS idx_pix_event     ON pixel_events (event, received_at DESC);
-- SELECT create_hypertable('pixel_events','received_at', if_not_exists => TRUE);

-- ===== Materialized: leaderboard de tippers (refresh por job) =====
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_tipper_leaderboard AS
SELECT
    tipper_username,
    COUNT(*)                                   AS tip_count,
    SUM(amount)                                AS total_tokens,
    AVG(amount)::numeric(12,2)                 AS avg_tip,
    MAX(amount)                                AS max_tip,
    COUNT(DISTINCT room_slug)                  AS rooms_tipped,
    MIN(occurred_at)                           AS first_tip_at,
    MAX(occurred_at)                           AS last_tip_at,
    MAX(occurred_at) - MIN(occurred_at)        AS active_span,
    now()                                      AS computed_at
FROM tip_events
WHERE occurred_at > now() - INTERVAL '30 days'
GROUP BY tipper_username
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS mv_tipper_lb_uq ON mv_tipper_leaderboard (tipper_username);

-- ===== Materialized: leaderboard de modelos por recaudo =====
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_model_earnings AS
SELECT
    m.room_slug,
    m.username,
    m.display_name,
    COALESCE(SUM(t.amount), 0)                 AS total_tokens_30d,
    COUNT(t.id)                                AS tip_count_30d,
    AVG(s.viewers)::numeric(10,1)              AS avg_viewers_7d,
    MAX(s.viewers)                             AS peak_viewers_7d
FROM models m
LEFT JOIN tip_events t
       ON t.room_slug = m.room_slug
      AND t.occurred_at > now() - INTERVAL '30 days'
LEFT JOIN room_snapshots s
       ON s.room_slug = m.room_slug
      AND s.scraped_at > now() - INTERVAL '7 days'
GROUP BY m.room_slug, m.username, m.display_name
WITH DATA;
CREATE UNIQUE INDEX IF NOT EXISTS mv_model_earn_uq ON mv_model_earnings (room_slug);

-- Refresh function para los materialized views (llamada por el scheduler)
CREATE OR REPLACE FUNCTION refresh_leaderboards() RETURNS void AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_tipper_leaderboard;
    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_model_earnings;
END;
$$ LANGUAGE plpgsql;
