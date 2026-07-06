"""
Grafo de detalle de sala: viewers, tokens de sesión, top tipper, últimos tips.

Muchos cam sites renderizan el chat público en el HTML inicial o lo cargan vía
WebSocket. Aquí scrapeamos el snapshot HTML; los tips en vivo los captura
ws_listener.py por separado.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import httpx
from scrapegraphai.graphs import SmartScraperGraph

from config import settings
from scraper.http_utils import fetch_html, http_session
from scraper.models import RoomDetail

log = logging.getLogger(__name__)

ROOM_PROMPT = """
Extract details about THIS room/model page:
- username, room_slug, display_name
- viewers (current viewer count, int)
- followers (int or null)
- room_status (online|offline|private|away)
- session_started_at (ISO datetime if visible, else null)
- session_tokens (total tokens tipped this session, int or 0)
- top_tipper_session (username of the biggest tipper this session, or null)
- recent_tips: list of the last visible public tip events, each with:
    tipper_username, amount (int), currency, message (or null), occurred_at (ISO or null)
Only use data actually visible on the page. Do not fabricate.
"""


def _build_graph(html: str) -> SmartScraperGraph:
    cfg = {"llm": settings.llm_config, "verbose": False, "headless": True}
    return SmartScraperGraph(
        prompt=ROOM_PROMPT,
        source=html,
        schema=RoomDetail,
        config=cfg,
    )


async def scrape_room(room_slug: str, *, client: httpx.AsyncClient) -> RoomDetail | None:
    url = urljoin(settings.target_base_url + "/", room_slug)
    log.info("scrapeando room url=%s", url)
    html = await fetch_html(url, client=client)
    graph = _build_graph(html)
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, graph.run)
    except Exception as e:
        log.error("grafo room %s falló: %s", room_slug, e)
        return None
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        return None
    result.setdefault("room_slug", room_slug)
    try:
        return RoomDetail.model_validate(result)
    except Exception as e:
        log.warning("room %s no validó: %s — %s", room_slug, e, result)
        return None


async def scrape_rooms(room_slugs: list[str]) -> list[RoomDetail]:
    out: list[RoomDetail] = []
    # Procesa en batches pequeños para respetar el rate-limit
    batch = settings.scrape_concurrency
    async with http_session() as client:
        for i in range(0, len(room_slugs), batch):
            chunk = room_slugs[i : i + batch]
            results = await asyncio.gather(
                *(scrape_room(s, client=client) for s in chunk),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, RoomDetail):
                    out.append(r)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    slugs = sys.argv[1:]
    if not slugs:
        print("uso: python -m scraper.graph_room slug1 slug2")
        sys.exit(0)
    for d in asyncio.run(scrape_rooms(slugs)):
        print(d.model_dump_json(indent=2))
