"""
Utilidades anti-bloqueo y de sesión HTTP compartidas por todos los grafos.

Estrategia (defensiva, no ofensiva):
  - Delay entre requests (rate-limit auto-impuesto)
  - Rotación de User-Agent
  - Header order realista
  - Proxy opcional (residencial) si SCRAPE_USE_PROXY=true
  - Retries con backoff exponencial (tenacity)
  - Cache en disco de páginas estáticas (perfil) para no re-scrapear
"""
from __future__ import annotations

import asyncio
import random
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fake_useragent import UserAgent
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings

_ua = UserAgent()

# Headers base realistas (orden preservado por httpx con un dict normal en 3.7+)
def _base_headers() -> dict[str, str]:
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }


def pick_user_agent() -> str:
    if not settings.scrape_user_agent_rotation:
        return "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    try:
        return _ua.random
    except Exception:
        return _base_headers  # type: ignore[return-value]


def proxy_url() -> str | None:
    if not settings.scrape_use_proxy:
        return None
    # Lee desde env extra si quieres un pool; aquí placeholder.
    import os
    return os.getenv("SCRAPE_PROXY_URL")


@retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type(
        (httpx.TransportError, httpx.HTTPStatusError, asyncio.TimeoutError)
    ),
)
async def fetch_html(url: str, *, client: httpx.AsyncClient) -> str:
    """GET con retry. Respeta el delay global para ser cortés."""
    headers = _base_headers()
    headers["User-Agent"] = pick_user_agent()
    # Jitter ±30% del delay para no ser perfectamente periódico
    delay = settings.scrape_request_delay * random.uniform(0.7, 1.3)
    await asyncio.sleep(delay)

    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30)
    if resp.status_code in (429, 503):
        # Backoff agresivo si el sitio pide calma
        retry_after = float(resp.headers.get("Retry-After", "10"))
        await asyncio.sleep(retry_after)
        raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
    resp.raise_for_status()
    return resp.text


@asynccontextmanager
async def http_session() -> AsyncIterator[httpx.AsyncClient]:
    """Client HTTP reutilizable con limits y cookies persistentes."""
    limits = httpx.Limits(
        max_connections=settings.scrape_concurrency * 2,
        max_keepalive_connections=settings.scrape_concurrency,
    )
    transport = httpx.AsyncHTTPTransport(retries=1, http2=True)
    async with httpx.AsyncClient(
        http2=True,
        limits=limits,
        transport=transport,
        proxy=proxy_url(),
        timeout=httpx.Timeout(30.0, connect=10.0),
    ) as client:
        yield client


class RateLimiter:
    """Token-bucket async simple para limitar RPM globales."""

    def __init__(self, rpm: int = 30):
        self._min_interval = 60.0 / rpm
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self._min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# Singleton global
rate_limiter = RateLimiter(rpm=int(60 / max(settings.scrape_request_delay, 1.0)))
