"""
Inicializa la base de datos: crea el schema desde db/schema.sql y,
opcionalmente, habilita TimescaleDB si está instalado.

Uso:
  python scripts/init_db.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import text

from db.database import engine

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


async def init() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    # SQLAlchemy no ejecuta multi-statement con text() en una sola call → splitear
    # pero los CREATE EXTENSION etc. vienen separados por ';'
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    async with engine.begin() as conn:
        # Intenta TimescaleDB (falla silenciosamente si no está instalado)
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb;"))
            print("✓ TimescaleDB habilitado")
        except Exception as e:
            print(f"· TimescaleDB no disponible (ok): {e}")
        for stmt in statements:
            try:
                await conn.execute(text(stmt))
            except Exception as e:
                print(f"· statement skipped: {e}")
        # Hypertables (mejor tarde, tras crear tablas)
        for tbl in ("room_snapshots", "tip_events", "schedule_observations", "pixel_events"):
            try:
                col = "scraped_at" if tbl == "room_snapshots" else (
                    "occurred_at" if tbl == "tip_events" else (
                        "observed_at" if tbl == "schedule_observations" else "received_at"
                    )
                )
                await conn.execute(text(
                    f"SELECT create_hypertable('{tbl}','{col}', if_not_exists => TRUE);"
                ))
                print(f"✓ hypertable {tbl}")
            except Exception as e:
                print(f"· hypertable {tbl} skip: {e}")
    print("\n✅ Schema inicializado.")


if __name__ == "__main__":
    if "--help" in sys.argv:
        print("Inicializa la BD ejecutando db/schema.sql")
        sys.exit(0)
    asyncio.run(init())
