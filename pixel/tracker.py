"""
Analytics Pixel — el "Meta Pixel" de statebate-pulse.

Endpoint que sirve un GIF 1x1 transparente y registra el evento.
Uso desde cualquier página promo (de la modelo o de un afiliado):

  <img src="https://pulse.tudominio.com/p/track.gif?room=alice&event=view&vid=abc123"
       width="1" height="1" alt="" style="display:none" />

  // O vía JS fetch (POST para eventos con payload):
  fetch('https://pulse.tudominio.com/p/track', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({room:'alice', event:'tip', amount:100, vid:'abc123'})
  })

El pixel también acepta webhooks del scraper (event=tip con payload) para
unificar la telemetría en una sola tabla (pixel_events).

Eventos soportados: view | click | land | tip | conversion | share | go_live
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from config import settings
from db.repository import insert_pixel_event

log = logging.getLogger(__name__)

router = APIRouter(prefix="/p", tags=["pixel"])

# GIF 1x1 transparente cacheado (no lo regeneramos por request)
_TRANSPARENT_GIF = base64.b64decode(
    b"R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)
GIF_HEADERS = {
    "Content-Type": "image/gif",
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Access-Control-Allow-Origin": "*",
}

COOKIE_NAME = "pulse_vid"
COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 año


def _ensure_vid(request: Request) -> str:
    """Lee o crea el viewer_id anónimo (cookie first-party)."""
    vid = request.cookies.get(COOKIE_NAME)
    if not vid:
        vid = secrets.token_urlsafe(16)
    return vid


def _set_vid_cookie(resp: Response, vid: str) -> None:
    resp.set_cookie(
        COOKIE_NAME, vid,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.is_prod,
        samesite="lax",
        path="/",
    )


def _client_ip(request: Request) -> str | None:
    # Detrás de Caddy/Cloudflare, la IP real viene en X-Forwarded-For
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


VALID_EVENTS = {"view", "click", "land", "tip", "conversion", "share", "go_live"}


def _parse_event(params: dict) -> dict:
    """Normaliza query params / body a un registro de pixel_event."""
    event = (params.get("event") or "view").lower()
    if event not in VALID_EVENTS:
        event = "view"
    return {
        "room_slug": params.get("room") or params.get("room_slug"),
        "event": event,
        "viewer_id": params.get("vid"),
        "referrer": params.get("referrer") or params.get("ref"),
        "affiliate_id": params.get("aff") or params.get("ref_code"),
        "payload": {k: v for k, v in params.items()
                    if k not in {"room", "room_slug", "event", "vid", "referrer",
                                 "ref", "aff", "ref_code"}},
    }


@router.get("/track.gif")
async def track_gif(request: Request) -> Response:
    """Pixel beacon GET. Devuelve GIF 1x1 y registra el evento."""
    params = dict(request.query_params)
    data = _parse_event(params)
    data["viewer_id"] = data["viewer_id"] or _ensure_vid(request)
    try:
        await insert_pixel_event(
            room_slug=data["room_slug"],
            event=data["event"],
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            referrer=data["referrer"],
            viewer_id=data["viewer_id"],
            payload=data["payload"],
            affiliate_id=data["affiliate_id"],
        )
    except Exception as e:
        log.error("pixel GET falló: %s", e)
    resp = Response(content=_TRANSPARENT_GIF, headers=GIF_HEADERS)
    if not request.cookies.get(COOKIE_NAME):
        _set_vid_cookie(resp, data["viewer_id"])
    return resp


@router.post("/track")
async def track_post(request: Request) -> JSONResponse:
    """Pixel beacon POST (para eventos con payload rico, e.g. tip).
    Requiere header X-Pulse-Sig = HMAC-SHA256(PIXEL_SECRET, body) si hay
    event=tip|conversion (para que no cualquiera inyecte conversiones falsas)."""
    try:
        body = await request.json()
    except Exception:
        body = dict(request.query_params)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid body"}, status_code=400)

    data = _parse_event(body)
    data["viewer_id"] = data["viewer_id"] or _ensure_vid(request)

    # Verificación HMAC para eventos sensibles
    if data["event"] in {"tip", "conversion"}:
        sig = request.headers.get("x-pulse-sig", "")
        raw = await request.body() if request.headers.get("content-type", "").startswith("application/json") else str(body).encode()
        expected = hmac.new(
            settings.pixel_secret.get_secret_value().encode(), raw, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return JSONResponse({"ok": False, "error": "bad signature"}, status_code=403)

    try:
        await insert_pixel_event(
            room_slug=data["room_slug"],
            event=data["event"],
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
            referrer=data["referrer"],
            viewer_id=data["viewer_id"],
            payload=data["payload"],
            affiliate_id=data["affiliate_id"],
        )
    except Exception as e:
        log.error("pixel POST falló: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    return JSONResponse({"ok": True, "event": data["event"]})


@router.get("/pixel.js")
async def pixel_js(request: Request) -> Response:
    """Snippet JS auto-instalable para embeber en páginas promo.

    Uso:
      <script src="https://pulse.tudominio.com/p/pixel.js" data-room="alice" async></script>
    """
    js = f"""
(function(){{
  var s=document.currentScript;
  var room=s.getAttribute('data-room')||'';
  var vid=(document.cookie.match(/(?:^|; )pulse_vid=([^;]+)/)||[])[1]||'';
  var base='{settings.app_base_url}';
  function send(ev,extra){{
    var q=new URLSearchParams(Object.assign({{room:room,event:ev,vid:vid}},extra||{{}}));
    // beacon no bloquea navegación
    if(navigator.sendBeacon) navigator.sendBeacon(base+'/p/track.gif?'+q.toString());
    else new Image().src=base+'/p/track.gif?'+q.toString();
  }}
  window.Pulse={{track:send}};
  send('view');
  document.addEventListener('click',function(e){{
    var t=e.target.closest('a[data-pulse]'); if(!t) return;
    send('click',{{href:t.href}});
  }});
}})();
"""
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600",
                             "Access-Control-Allow-Origin": "*"})


@router.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}
