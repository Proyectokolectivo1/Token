"""
Listener de WebSocket para capturar tips en tiempo real.

Los cam sites típicamente usan un WS para push de eventos del chat público
(tips, mensajes, joins). Interceptamos esos frames y los persistimos como
TipEvent. Esto complementa el scraping HTML (que solo ve el snapshot).

Notas:
  - La URL del WS y el protocolo de handshake varían por sitio. Se configura
    via TARGET_WS_URL en .env (descubierto inspeccionando DevTools del navegador).
  - Usamos `websockets` (async, puro Python).
  - Mantenemos el listener corriendo como tarea background en el API o como
    proceso aparte en Docker.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import websockets
from websockets.asyncio.client import connect

from scraper.http_utils import pick_user_agent
from scraper.models import TipEvent

log = logging.getLogger(__name__)

# Estructura de mensaje típica (placeholder; ajustar al protocolo real del sitio):
#   {"type": "tip", "user": "john", "amount": 50, "message": "hi", "room": "alice"}
#   {"type": "tip", "data": {"username": "...", "amount": ...}}
# Normalizamos varios shapes.

WS_URL = os.getenv("TARGET_WS_URL", "wss://statebate.com/chat/ws")
RECONNECT_DELAY = 5.0


def _extract_tip(msg: dict, default_room: str | None = None) -> TipEvent | None:
    """Heurística tolerante para distintos shapes de mensaje de tip."""
    if msg.get("type") not in ("tip", "tip_alert", "tipEvent"):
        return None
    data = msg.get("data") or msg
    user = data.get("user") or data.get("username") or data.get("from")
    amount = data.get("amount") or data.get("tokens") or data.get("value")
    room = data.get("room") or data.get("room_slug") or default_room
    if not user or not amount or not room:
        return None
    try:
        amount_i = int(amount)
    except (TypeError, ValueError):
        return None
    if amount_i <= 0:
        return None
    ts = data.get("ts") or data.get("timestamp") or data.get("occurred_at")
    occurred = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
    return TipEvent(
        tipper_username=str(user),
        amount=amount_i,
        currency=str(data.get("currency", "tokens")),
        message=data.get("message"),
        occurred_at=occurred,
        room_slug=str(room),
    )


async def _persist(tip: TipEvent) -> None:
    """Inserta el tip en la DB. Import diferido para evitar ciclos."""
    from db.repository import insert_tip_event
    try:
        await insert_tip_event(tip)
        log.debug("tip persistido: %s + %d (%s)", tip.tipper_username, tip.amount, tip.room_slug)
    except Exception as e:
        log.error("no se pudo persistir tip: %s", e)


async def listen_room(room_slug: str, *, stop_event: asyncio.Event) -> None:
    """Escucha el WS de una sala hasta que stop_event se setee."""
    url = f"{WS_URL}?room={room_slug}"
    headers = {"User-Agent": pick_user_agent(), "Origin": "https://statebate.com"}
    backoff = RECONNECT_DELAY
    while not stop_event.is_set():
        try:
            async with connect(url, additional_headers=headers, ping_interval=20) as ws:
                log.info("WS conectado: room=%s", room_slug)
                backoff = RECONNECT_DELAY
                async for raw in ws:
                    if stop_event.is_set():
                        break
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    tip = _extract_tip(msg, default_room=room_slug)
                    if tip:
                        await _persist(tip)
        except Exception as e:
            log.warning("WS %s desconectado (%s), reintentando en %.1fs", room_slug, e, backoff)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                return
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 60.0)


class WSListenerPool:
    """Gestiona varios listeners de sala concurrentes."""

    def __init__(self, max_concurrent: int = 10) -> None:
        self._stops: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._max = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)

    async def watch(self, room_slug: str) -> None:
        async with self._sem:
            stop = asyncio.Event()
            self._stops[room_slug] = stop
            try:
                await listen_room(room_slug, stop_event=stop)
            finally:
                self._stops.pop(room_slug, None)

    def add(self, room_slug: str) -> None:
        if room_slug in self._tasks:
            return
        self._tasks[room_slug] = asyncio.create_task(self.watch(room_slug))

    def remove(self, room_slug: str) -> None:
        stop = self._stops.get(room_slug)
        if stop:
            stop.set()
        task = self._tasks.pop(room_slug, None)
        if task:
            task.cancel()

    async def stop_all(self) -> None:
        for stop in self._stops.values():
            stop.set()
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()
        self._stops.clear()


# Singleton
ws_pool = WSListenerPool()
