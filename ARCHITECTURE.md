# Arquitectura de statebate-pulse

## Diagrama de componentes

```
                         ┌──────────────────────────┐
                         │   GitHub Actions (cron)  │
                         │   cada 15 min → POST     │
                         │   /api/admin/scrape/once │
                         └────────────┬─────────────┘
                                      │ HTTPS (X-Api-Key)
                                      ▼
┌───────────────────────────────────────────────────────────────────┐
│                  Oracle Cloud VM (Always Free ARM A1)             │
│                                                                   │
│  ┌─────────────┐   ┌────────────────┐   ┌──────────────────────┐ │
│  │   Caddy     │──▶│   App (FastAPI)│──▶│  PostgreSQL 16       │ │
│  │  TLS + CORS │   │  • REST /api   │   │  + TimescaleDB       │ │
│  │  reverse px │   │  • Pixel /p    │   │  • mv_tipper_lb      │ │
│  └─────────────┘   │  • APScheduler │   │  • mv_model_earnings │ │
│         ▲          │  • WS pool     │   │  • hypertables       │ │
│         │          └───────┬────────┘   └──────────────────────┘ │
│         │                  │                                       │
│         │    ┌─────────────┴──────────────┐                      │
│         │    │  Scraper (ScrapegraphAI)   │                      │
│         │    │  • graph_search (listing)  │                      │
│         │    │  • graph_room   (detalle)  │                      │
│         │    │  • graph_profile (bio)     │                      │
│         │    │  • ws_listener (tips live) │                      │
│         │    └────────────────────────────┘                      │
│         │                                                          │
│         │    ┌────────────────────────────────────────┐          │
│         │    │  Algorithms                            │          │
│         │    │  • top_tippers (score compuesto)       │          │
│         │    │  • schedule_matcher (coseno 168-dim)   │          │
│         │    │  • traffic_booster (score + reco)      │          │
│         │    └────────────────────────────────────────┘          │
└─────────┼──────────────────────────────────────────────────────────┘
          │
          │  /p/track.gif  (GIF 1x1 + cookie pulse_vid)
          │  /p/pixel.js   (snippet auto-instalable)
          │
          ▼
┌───────────────────────────────────────────────────────────────────┐
│  Páginas promo de modelos/afiliados (Twitter, Linktree, sitio)    │
│  <img src=".../p/track.gif?room=alice&event=view">                │
└───────────────────────────────────────────────────────────────────┘

          ┌─────────────────────────────────────────────┐
          │  GitHub Pages (dashboard SPA estático)      │
          │  https://usuario.github.io/statebate-pulse  │
          │  → fetch JSON desde la API de Oracle Cloud  │
          └─────────────────────────────────────────────┘
```

## Flujo de datos

1. **Listado** (`graph_search`): pagina el directorio público de salas online → `models` (upsert) + `schedule_observations` (insert).
2. **Detalle** (`graph_room`): por cada sala online, extrae viewers, tokens de sesión, últimos N tips → `room_snapshots` + `tip_events`.
3. **Perfil** (`graph_profile`): 1x/día para modelos con perfil stale → actualiza `models.bio/declared_schedule/tags`.
4. **Tips en vivo** (`ws_listener`): conexión WebSocket persistente por sala → `tip_events` en tiempo real (dedupe por UNIQUE constraint).
5. **Pixel** (`/p/track*`): eventos de view/click/tip desde páginas promo → `pixel_events`.
6. **Agregados**: cada 15 min `refresh_leaderboards()` refresca los materialized views.
7. **Algoritmos**: leen tablas crudas + MVs → exponen vía `/api/*`.

## Modelo de datos (tablas)

| Tabla | Granularidad | Hipertable |
|-------|-------------|------------|
| `models` | 1 fila por modelo | no |
| `room_snapshots` | 1 fila por scrapeo de sala | sí (`scraped_at`) |
| `tip_events` | 1 fila por tip | sí (`occurred_at`) |
| `schedule_observations` | 1 fila por muestreo online/offline | sí (`observed_at`) |
| `pixel_events` | 1 fila por evento de pixel | sí (`received_at`) |
| `mv_tipper_leaderboard` | 1 fila por tipper (30d) | materialized view |
| `mv_model_earnings` | 1 fila por modelo (30d) | materialized view |

## Algoritmo de Top Tippers (detalle)

Score compuesto en [0, 1], suma de 5 componentes normalizados con sigmoid:

```
score = 0.40·σ(z_volume)    # tokens totales (más peso)
      + 0.20·σ(z_frequency) # cantidad de tips
      + 0.15·σ(-z_recency)  # último tip más reciente = mejor
      + 0.15·σ(z_loyalty)   # días distintos activo
      + 0.10·σ(z_breadth)   # cuántas modelos distintas tipea
```

donde `z_x = (x - μ_x) / σ_x` (z-score) y `σ` = sigmoid.

**Pesos ajustables** en `algorithms/top_tippers.py::W`. Subir `recency` premia
tippers "calientes ahora"; subir `loyalty` premia "whales" estables.

Para UNA modelo específica, `top_tippers_for_model()` combina:
- historial directo (tokens a esa modelo × 3)
- afinidad horaria (tokens dados a cualquier modelo en los slots donde esta modelo suele transmitir)

## Algoritmo de Schedule Matcher

- Cada modelo → vector de 168 dims (7 días × 24 h) con viewers promedio.
- Cada tipper → vector de 168 dims con conteo de tips por slot.
- Match score = **similitud coseno** entre ambos.
- `best_time_to_go_live()`: el slot (dow, hour) con más tippers activos globalmente
  donde la modelo NO suele estar online → máxima oportunidad de captar nuevos.

## "Pixel de Meta" — cómo funciona el boost de tráfico

| Análogo Meta Pixel | statebate-pulse |
|---|---|
| JS en TU web | `/p/pixel.js` snippet en páginas promo de modelos/afiliados |
| `fbq('track','Purchase')` | `POST /p/track {event:'tip'}` (firma HMAC) |
| Cookie `_fbp` | Cookie `pulse_vid` (1ª parte, 1 año, httpOnly) |
| Conversión = compra | Conversión = tip (medido por el scraper, no por el pixel) |
| Optimización de anuncios | Recomendaciones: mejor slot, cross-promo, link afiliado |

**El "boost" no es inyección de bots**: es medir + recomendar. El tráfico real
llega por:
1. **SEO del dashboard** (GitHub Pages indexa "top modelos online" → tráfico orgánico).
2. **Cross-promo** entre modelos con audiencias complementarias (recomendado por el algoritmo).
3. **Enlaces de afiliado** atribuidos (si el sitio tiene programa de afiliados).
4. **Timing óptimo**: la modelo conecta en el slot con más tippers activos.

## Límites y seguridad

- **Rate limit auto-impuesto**: `SCRAPE_REQUEST_DELAY=2.0s` (≤ 30 RPM).
- **Deduplicación**: `UNIQUE(room_slug, tipper_username, occurred_at, amount)` en tips.
- **HMAC** en eventos `tip`/`conversion` del pixel (evita inyección de conversiones falsas).
- **API_KEY** en endpoints admin.
- **No PII**: usernames se guardan tal cual aparecen en público; `tipper_hash` (sha256 truncado) disponible para reportes anonimizados.
- **TLS** automático vía Caddy (Let's Encrypt).
