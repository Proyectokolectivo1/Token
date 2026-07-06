# statebate-pulse

> Sistema de scraping, analítica y boosting de tráfico para salas de transmisión en cam sites.
> Inspirado en [show-guiones/campulse](https://github.com/show-guiones/campulse).

## 🎯 Objetivo

Construir una herramienta que:

1. **Extraiga** datos públicos de salas y modelos (viewers, tips, horarios, perfiles).
2. **Ranquee** tippers y modelos por recaudo, frecuencia y horarios de conexión.
3. **Mida** el tráfico de cada sala con un "pixel" analítico (análogo al Meta Pixel).
4. **Recomiende** horarios óptimos de transmisión y empareje tippers top con perfiles afines.

## 🧱 Stack

| Capa | Tecnología | Justificación |
|------|------------|---------------|
| Scraping | **ScrapegraphAI** + Playwright | LLM-driven extraction; tolerante a cambios de DOM |
| Backend | **FastAPI** (Python 3.11) | Async, ligero, ideal para API + pixel endpoint |
| DB | **PostgreSQL 16** + **TimescaleDB** | Series temporales para métricas de tráfico |
| Scheduler | **APScheduler** | Cron in-process, simple para VM única |
| Frontend | **GitHub Pages** (SPA estática) | Sin costo, CDN global, deploy automático |
| CI/CD | **GitHub Actions** | Cron de scraping + build/deploy del dashboard |
| Infra | **Oracle Cloud** (VM Always Free ARM) | 4 vCPU / 24 GB RAM gratis |

## 📁 Estructura

```
statebate-pulse/
├── scraper/              # Pipelines de ScrapegraphAI + Playwright
│   ├── graph_search.py   # Grafo de búsqueda de modelos online
│   ├── graph_room.py     # Grafo de detalle de sala (tips, viewers)
│   ├── graph_profile.py  # Grafo de perfil de modelo
│   ├── ws_listener.py    # Listener WebSocket para eventos de tip en vivo
│   └── runner.py         # Orquestador del scraping periódico
├── db/                   # Esquema, migraciones, cliente
├── algorithms/           # Top Tippers + Schedule Matcher + Traffic Booster
├── pixel/                # Analytics Pixel (beacon 1x1 + ingest)
├── api/                  # FastAPI (REST + pixel + webhooks)
├── dashboard/            # SPA estática para GitHub Pages
├── deploy/               # Docker, Caddy, setup Oracle Cloud
├── .github/workflows/    # CI: scrape cron + deploy dashboard
└── scripts/              # Utilidades CLI
```

## 🚀 Quickstart local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # editar credenciales
python scripts/init_db.py       # crear tablas
python -m api.main              # levantar API en :8080
python -m scraper.runner        # corrida manual de scraping
```

## ⚖️ Aviso legal y ético

Este proyecto **solo extrae datos públicamente visibles** (room listings, contadores de
viewers, eventos de tip mostrados en el chat público). No accede a áreas privadas,
no almacena PII real (nombres, correos, pagos), y agrega/hashing de usernames.

Antes de operar en producción:
- Revisa los **Términos de Servicio** del sitio objetivo.
- Respeta `robots.txt` y rate-limits razonables (≤ 1 req/2s por IP).
- Considers usar **un affiliate ID oficial** si el sitio ofrece programa de afiliados:
  es la vía legítima para "boostear" tráfico y monetizar.
- No uses los datos para acosar, doxxear o deanonymizar tippers individuales.

## 📜 Licencia

MIT — ver `LICENSE`. Uso bajo responsabilidad del operador.
