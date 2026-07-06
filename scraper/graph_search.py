"""
Grafo de búsqueda: lista todas las salas online paginando el listado público.

Usa ScrapegraphAI con un SmartScraperGraph que fuerza el schema RoomListItem.
El LLM solo se ocupa de mapear HTML -> JSON estructurado; la descarga la hacemos
nosotros con httpx (más rápido y controlable que dejarlo a ScrapegraphAI).
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import httpx
from scrapegraphai.graphs import SmartScraperGraph

from config import settings
from scraper.http_utils import fetch_html, http_session
from scraper.models import RoomListItem

log = logging.getLogger(__name__)

# Prompt del LLM: específico, en inglés (mejor rendimiento de extracción).
SEARCH_PROMPT = """
From the page, extract the list of all currently online rooms/models.
For each room return: username, room_slug (the URL path segment of the room),
display_name, gender (female|male|couple|trans or null), age (int or null),
country, viewers (int), room_status (online|offline|private|away), tags (list),
thumbnail_url, is_hd (bool).
Only include rooms actually present on the page. Do not invent entries.
If a field is not visible, use null or default.
"""


def _build_graph(html: str, source_url: str) -> SmartScraperGraph:
    graph_config = {
        "llm": settings.llm_config,
        "verbose": False,
        "headless": True,
        # Le pasamos el HTML directamente => no navega
    }
    return SmartScraperGraph(
        prompt=SEARCH_PROMPT,
        source=html,
        schema=RoomListItem,
        config=graph_config,
    )


async def scrape_page(page: int, *, client: httpx.AsyncClient) -> list[RoomListItem]:
    """Scrapea una página del listado y devuelve las salas."""
    path = settings.target_room_list_path.format(page=page)
    url = urljoin(settings.target_base_url + "/", path.lstrip("/"))
    log.info("scrapeando listado page=%d url=%s", page, url)
    html = await fetch_html(url, client=client)

    # ScrapegraphAI es síncrono (usa playwright bajo el hood) → offload a thread
    graph = _build_graph(html, url)
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, graph.run)

    # result puede ser dict o lista según versión; normalizar
    if isinstance(result, dict) and "rooms" in result:
        result = result["rooms"]
    if not isinstance(result, list):
        result = [result]

    items: list[RoomListItem] = []
    for r in result:
        try:
            items.append(RoomListItem.model_validate(r))
        except Exception as e:
            log.warning("item inválido descartado: %s — %s", e, r)
    return items


async def scrape_all_online(max_pages: int = 10) -> list[RoomListItem]:
    """Scrapea todas las páginas hasta que una venga vacía o llegue a max_pages."""
    out: list[RoomListItem] = []
    async with http_session() as client:
        for page in range(1, max_pages + 1):
            try:
                rooms = await scrape_page(page, client=client)
            except Exception as e:
                log.error("page %d falló: %s", page, e)
                break
            if not rooms:
                log.info("página %d vacía, fin del listado", page)
                break
            out.extend(rooms)
            # Stop temprano si las páginas están paginadas raras
            if len(rooms) < 5:
                break
    # Deduplica por room_slug (a veces aparece en varias páginas)
    seen: dict[str, RoomListItem] = {}
    for r in out:
        seen[r.room_slug] = r
    return list(seen.values())


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    data = asyncio.run(scrape_all_online(max_pages=n))
    print(f"{len(data)} salas online")
    for r in data[:5]:
        print(f"  - {r.username} ({r.viewers} viewers) [{r.room_status.value}]")
