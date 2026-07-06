# Roadmap

Estado: MVP funcional (scaffold completo). Las fases están ordenadas por valor/riesgo.

## ✅ Fase 0 — MVP (listo en este repo)

- [x] Scraper ScrapegraphAI (search/room/profile) con anti-bloqueo básico
- [x] WS listener para tips en vivo (dedupe por UNIQUE)
- [x] DB PostgreSQL + TimescaleDB + materialized views
- [x] API FastAPI (REST + pixel + scheduler)
- [x] Algoritmos: Top Tippers (score compuesto), Schedule Matcher (coseno), Traffic Booster
- [x] Dashboard GitHub Pages (vanilla JS)
- [x] GitHub Actions (cron scrape + deploy pages)
- [x] Docker + Caddy para Oracle Cloud

## 🚧 Fase 1 — Endurecimiento (semana 1-2)

- [ ] **Audit legal**: leer ToS del sitio, decidir affiliate-only vs scraping.
- [ ] Respetar `robots.txt` programáticamente (fetch + parser antes de scrape).
- [ ] Políticas de retención de datos (job de purge a 90 días).
- [ ] Opt-out endpoint `/api/opt-out`.
- [ ] Tests: al menos happy-path de cada grafo con HTML fixtures.
- [ ] Métricas Prometheus en `/metrics` (requests, latency, scrape success rate).
- [ ] Alertas: Slack/Telegram si el scraper falla 3 ciclos seguidos.

## 🔜 Fase 2 — Features de valor (semana 3-4)

- [ ] **Heatmap visual** 7×24 en el modal de modelo (canvas/SVG).
- [ ] **Predicción de peak**: regresión lineal sobre historial de viewers para
      predecir el peak de la próxima sesión.
- [ ] **Tipper retention curve**: probabilidad de que un tipper vuelva en D+1/7/30.
- [ ] **Affiliate attribution**: JOIN pixel_events × tip_events para medir
      conversión real de cada link de afiliado.
- [ ] **Notifications**: webhook a la modelo cuando su traffic_score cae < 30.
- [ ] **A/B testing de horarios**: recomendar 2 slots alternativos y medir cuál rinde.

## 🌐 Fase 3 — Escala (mes 2+)

- [ ] **Migrar a Ollama local** en la VM para LLM $0 (Llama 3.1 8B es suficiente
      para extracción estructurada simple).
- [ ] **Pool de proxies residenciales** si el sitio bloquea la IP del DC.
- [ ] **Redis** para cache de respuestas de API (aliviar Postgres en dashboard).
- [ ] **Multi-sitio**: abstraer `TARGET_SITE` en un adapter por sitio
      (statebate, stripchat, chaturbate, etc.) — mismo pipeline, distinto adapter.
- [ ] **Mobile PWA** del dashboard (manifest + service worker).

## 🧪 Fase 4 — Calidad de datos

- [ ] **Detección de outliers**: tips absurdos (1M tokens) → flag para revisión.
- [ ] **Reconciliación**: comparar `session_tokens` del snapshot vs `SUM(tip_events)`
      de la sesión; alertar si divergen > 20%.
- [ ] **Backfill**: re-scrapear historial público si el sitio lo permite (perfil
      con stats históricas).

## ⚠️ No-go explícito

- No almacenar imágenes/video/audio.
- No cruzar datos con redes sociales.
- No construir herramientas de contacto directo a tippers.
- No operar sin haber leído los ToS del sitio.
