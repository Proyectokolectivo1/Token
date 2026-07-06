# Despliegue en Oracle Cloud (VM Always Free ARM)

> Guía paso a paso. Tiempo estimado: 30-45 min.

## 1. Crear la VM (Always Free Ampere A1)

1. Entra a https://cloud.oracle.com → Compute → Instances → Create instance.
2. **Shape**: `VM.Standard.A1.Flex` (ARM). Asigna **4 OCPU / 24 GB RAM** (dentro del Always Free).
3. **Image**: Canonical Ubuntu 22.04 (o 24.04) aarch64.
4. **SSH keys**: genera o sube tu pública. **Guarda la privada**.
5. **VCN**: crea una con subnet pública. Asegura un **Public IP**.
6. **Security List / NSG**: abre puertos `80` (HTTP) y `443` (HTTPS) en ingress.
   - El `22` (SSH) ya viene abierto por defecto; restríngelo a tu IP.

## 2. DNS

- Crea un registro **A** `pulse.tudominio.com` → Public IP de la VM.

## 3. Preparar la VM

```bash
ssh ubuntu@pulse.tudominio.com

# Docker + compose plugin (oficial)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# re-login para que el grupo aplique

sudo apt-get update && sudo apt-get install -y git ufw fail2ban
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
sudo ufw enable
```

## 4. Clonar y configurar

```bash
git clone https://github.com/TU_USUARIO/statebate-pulse.git
cd statebate-pulse

cp .env.example .env
# Editar TODOS los campos:
#   DATABASE_URL no hace falta (lo setea docker-compose vía DB_PASSWORD)
#   DB_PASSWORD=<contraseña fuerte>
#   DOMAIN=pulse.tudominio.com
#   LLM_API_KEY=<tu key de OpenAI/Groq/Together>
#   PIXEL_SECRET=<aleatorio>
#   BOOST_AFFILIATE_ID=<tu ID del programa de afiliados del sitio, si aplica>
#   API_KEY=<aleatorio, para endpoints admin>
#   GITHUB_TOKEN=<PAT con repo:write para el webhook opcional>

# Verifica config
grep -vE '^\s*#|^\s*$' .env
```

## 5. Levantar

```bash
docker compose up -d --build
docker compose logs -f app    # verificar arranque
```

## 6. Inicializar DB (una sola vez)

```bash
docker compose exec app python scripts/init_db.py
```

## 7. Verificar

```bash
curl https://pulse.tudominio.com/p/healthz
# → {"ok":true,"ts":"..."}

curl https://pulse.tudominio.com/api/healthz
# → {"ok":true}

# Corrida manual de scraping:
curl -X POST https://pulse.tudominio.com/api/admin/scrape/once \
  -H "X-Api-Key: $API_KEY" -H "Content-Type: application/json" -d '{}'
```

## 8. Configurar GitHub

En tu repo → **Settings → Secrets and variables → Actions**:

| Secret | Valor |
|--------|-------|
| `PULSE_API_URL` | `https://pulse.tudominio.com` |
| `PULSE_API_KEY` | el mismo `API_KEY` del `.env` |

Esto habilita:
- `scrape-cron.yml` → cada 15 min llama a `/api/admin/scrape/once`.
- `deploy-pages.yml` → publica `dashboard/` en GitHub Pages.

## 9. Activar GitHub Pages

Repo → **Settings → Pages → Source: GitHub Actions**.
El siguiente push a `main` que toque `dashboard/` desplegará en
`https://TU_USUARIO.github.io/statebate-pulse/`.

## 10. Operación

- **Logs**: `docker compose logs -f app caddy`
- **Estado DB**: `docker compose exec db psql -U pulse -d pulse -c "\dt+"`
- **Refresh manual leaderboards**:
  `curl -X POST .../api/admin/refresh-leaderboards -H "X-Api-Key: $API_KEY"`
- **Backups**: añade un cron en la VM:
  ```bash
  0 3 * * * docker compose exec -T db pg_dump -U pulse pulse | gzip > /home/ubuntu/backups/pulse_$(date +\%F).sql.gz
  ```

## 11. Costo

- **Oracle Cloud Always Free**: 4 OCPU + 24 GB RAM + 200 GB block storage → **$0/mes**.
- **GitHub Pages**: gratis para repos públicos.
- **GitHub Actions**: 2000 min/mes gratis (el cron usa ~2 min/run × 96 runs/día ≈ 5760 min/mes → considera self-hosted runner o baja la frecuencia a cada 30 min).
- **LLM**: ScrapegraphAI hace ~1 call LLM por página. Con `gpt-4o-mini` a ~$0.15/1M input tokens, costo mensual estimado **< $5** si scrapeas < 500 salas/día. Para abaratar: usa **Groq/Llama 3.1** (gratis tier) o **Ollama local** en la VM (setea `LLM_PROVIDER=local`, `LLM_BASE_URL=http://localhost:11434/v1`).

## 12. Reducir consumo de GitHub Actions (opcional)

Si el cron de 15 min consume mucho de tu cuota, reemplázalo por el scheduler
interno del API (APScheduler ya está configurado en `api/main.py`). Solo comenta
el job de `scrape-cron.yml` o baja la frecuencia a cada hora. El scheduler interno
corre `run_once` cada 15 min sin tocar GitHub.
