"""
CLI de utilidades para statebate-pulse.

Uso:
  python -m scripts.cli --help
  python -m scripts.cli refresh         # refrescar materialized views
  python -m scripts.cli tipper <name>   # info de un tipper
  python -m scripts.cli boost <slug>    # reporte de boost para una modelo
  python -m scripts.cli match <slug>    # tippers afines por horario
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys

import click

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


@click.group()
def cli() -> None:
    """statebate-pulse CLI."""


@cli.command()
def refresh() -> None:
    """Refresca los materialized views (leaderboards)."""
    from db.repository import refresh_leaderboards
    asyncio.run(refresh_leaderboards())
    click.echo("✓ leaderboards refrescados")


@cli.command()
@click.argument("name")
def tipper(name: str) -> None:
    """Muestra el perfil de un tipper."""
    from algorithms.top_tippers import rank_tippers
    ranked = asyncio.run(rank_tippers(days=30, limit=1000))
    for i, t in enumerate(ranked, 1):
        if t.tipper_username == name.lower():
            click.echo(json.dumps(t.__dict__, default=str, indent=2))
            return
    click.echo(f"tipper '{name}' no encontrado en top 1000 (30d)")


@cli.command()
@click.argument("slug")
def boost(slug: str) -> None:
    """Reporte de boost de tráfico para una modelo."""
    from algorithms.traffic_booster import boost_report
    report = asyncio.run(boost_report(slug))
    click.echo(json.dumps(report, default=str, indent=2))


@cli.command()
@click.argument("slug")
@click.option("--limit", default=20)
def match(slug: str, limit: int) -> None:
    """Tippers afines por horario."""
    from algorithms.schedule_matcher import best_tippers_for_model
    items = asyncio.run(best_tippers_for_model(slug, limit=limit))
    click.echo(json.dumps(items, indent=2))


@cli.command()
@click.option("--limit", default=50)
def traffic(limit: int) -> None:
    """Leaderboard de tráfico en vivo."""
    from algorithms.traffic_booster import traffic_leaderboard
    scores = asyncio.run(traffic_leaderboard(limit=limit))
    for s in scores:
        click.echo(f"#{s.rank:>3}  {s.score:5.1f}  {s.room_slug:<30}  v={s.viewers_now}  +{s.viewers_growth_1h*100:.1f}%  {s.tokens_velocity_1h:.0f}tok/h")


if __name__ == "__main__":
    cli()
