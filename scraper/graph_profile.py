"""
Grafo de perfil: datos biográficos + horario declarado de una modelo.
El perfil suele ser una página pública con info agregada (menos volátil).
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin

import httpx
from scrapegraphai.graphs import SmartScraperGraph

from config import settings
from scraper.http_utils import fetch_html, http_session
from scraper.models import ProfileDetail

log = logging.getLogger(__name__)

PROFILE_PROMPT = """
Extract the public profile/bio info of this model:
- username, room_slug, display_name
- bio (free text, truncated to ~500 chars if very long)
- gender (female|male|couple|trans or null)
- age (int or null)
- country (name or ISO code)
- followers (int or 0)
- total_views (int or null)
- declared_schedule (any schedule/hours text shown, verbatim, e.g. "Mon-Fri 18:00-22:00 GMT")
- avatar_url
- tags (list of tags/categories shown on profile)
Use only what is visible. Do not guess.
"""


def _build_graph(html: str) -> SmartScraperGraph:
    cfg = {"llm": settings.llm_config, "verbose": False, "headless": True}
    return SmartScraperGraph(
        prompt=PROFILE_PROMPT,
        source=html,
        schema=ProfileDetail,
        config=cfg,
    )


async def scrape_profile(room_slug: str, *, client: httpx.AsyncClient) -> ProfileDetail | None:
    # La mayoría de cam sites: /profile/<slug> o el mismo /<slug> tiene la bio
    candidates = [
        urljoin(settings.target_base_url + "/", f"profile/{room_slug}"),
        urljoin(settings.target_base_url + "/", room_slug),
    ]
    for url in candidates:
        try:
            html = await fetch_html(url, client=client)
        except Exception as e:
            log.debug("profile url %s falló: %s", url, e)
            continue
        if not html or len(html) < 500:
            continue
        graph = _build_graph(html)
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, graph.run)
        except Exception as e:
            log.error("grafo profile %s falló: %s", room_slug, e)
            continue
        if isinstance(result, list):
            result = result[0] if result else {}
        if not isinstance(result, dict):
            continue
        result.setdefault("room_slug", room_slug)
        try:
            return ProfileDetail.model_validate(result)
        except Exception as e:
            log.warning("profile %s no validó: %s", room_slug, e)
    return None


async def scrape_profiles(slugs: list[str]) -> list[ProfileDetail]:
    out: list[ProfileDetail] = []
    batch = settings.scrape_concurrency
    async with http_session() as client:
        for i in range(0, len(slugs), batch):
            chunk = slugs[i : i + batch]
            results = await asyncio.gather(
                *(scrape_profile(s, client=client) for s in chunk),
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, ProfileDetail):
                    out.append(r)
    return out
