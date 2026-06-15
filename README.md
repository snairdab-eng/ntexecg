# NTEXECG — Signal Gateway

Gateway intermedio de señales de trading. Recibe señales de LuxAlgo, las evalúa
mediante un pipeline de 5 niveles (fail-fast), agrega Stop Loss obligatorio basado
en ATR, y reenvía solo las señales aprobadas a TradersPost con el SL incluido.

NTEXECG **no genera señales, no decide estrategias, no administra el portafolio.**
Su trabajo es filtrar señales de baja calidad y agregar protección de riesgo.

---

## Prerequisites by environment

### NTDEV (development)
- Windows Server 2025
- Git 2.54+ (LF line endings)
- Python 3.12+
- PostgreSQL 16 (local)
- VS Code + Claude Code
- VPN a NTEXECG

### NTEXECG (production)
- Ubuntu Server 24.04 LTS
- Docker Engine + Docker Compose
- Nginx + Certbot
- cifs-utils (montaje Samba)

### NTRADER (data source)
- Windows Server 2025
- NinjaTrader Desktop (feed Tradovate — CME)
- NTraderExecutionBridge.cs compilado y activo en cada chart

---

## NTDEV Setup

```powershell
# 1. Clonar el repo a C:\NTEXECG\
git clone <repo-url> C:\NTEXECG

# 2. Crear y activar el entorno virtual
python -m venv .venv
.venv\Scripts\Activate.ps1

# 3. Instalar dependencias (incluye extras de desarrollo)
pip install -e ".[dev]"

# 4. Crear .env desde la plantilla
copy .env.example .env

# 5. Crear la base de datos (PostgreSQL local)
createdb ntexecg

# 6. Aplicar migraciones
alembic upgrade head

# 7. Cargar datos de desarrollo (símbolos, perfiles, template)
python scripts/seed_dev_data.py

# 8. Levantar la app
uvicorn app.main:app --reload --port 8000

# 9. Abrir la UI
start http://localhost:8000/ui
```

> En NTDEV `MARKET_DATA_PROVIDER=yfinance` (datos delayed ~15 min). NTDEV nunca
> monta `\\NTRADER\bridge` (inestable vía VPN).

---

## Running tests

```powershell
pytest -v
pytest -v --cov=app --cov-report=term-missing
```

Todos los tests usan SQLite en memoria y `MockMarketDataProvider` —
nunca yfinance real ni el bridge real.

---

## Simulating a webhook

```powershell
python scripts/simulate_webhook.py `
  --strategy-id mes5m_confirmation_normal `
  --action sell --ticker MES --sentiment short `
  --price 5500.00 --interval 5 --token dev_global_token
```

El script envía el webhook y luego consulta la decisión (procesada en background),
mostrando el outcome y el SL calculado si fue APPROVE.

### Simulating an exit signal

```powershell
python scripts/simulate_webhook.py `
  --strategy-id mes5m_confirmation_normal `
  --exit --ticker MES --token dev_global_token
```

### Ver el payload sin enviar

```powershell
python scripts/simulate_webhook.py --strategy-id mes5m --ticker MES --dry
```

---

## DRY RUN mode

Con `DRY_RUN=true` (default), las señales aprobadas **no** se envían a TradersPost:
se registra un `WebhookDelivery` con status `DRY_RUN` y se actualiza el estado
estimado de posición, pero no se hace ninguna llamada HTTP real. La UI muestra un
badge naranja **DRY RUN** prominente mientras está activo.

Para enviar realmente a TradersPost: `DRY_RUN=false` y `TRADERSPOST_ENABLED=true`
en `.env` (solo después de verificar SL en cuentas paper).

---

## Deploying to NTEXECG via VPN

```bash
ssh usuario@ip-ntexecg-vpn \
  "cd ntexecg && git pull && \
   docker compose up -d --build && \
   docker compose exec app alembic upgrade head && \
   curl http://localhost:8000/health"
```

En NTEXECG (producción) `MARKET_DATA_PROVIDER=ninja_trader_bridge` y se monta
`\\NTRADER\bridge → /mnt/ntbridge` (read-only). Ver `scripts/mount_ntbridge.sh`
y el doc 07 para la configuración completa del servidor (fstab, credenciales, cron).

```bash
# Montar el bridge en NTEXECG
./scripts/mount_ntbridge.sh
```

---

## Checking contract rollovers

```powershell
python scripts/rollover_alert.py --days 7
```

Lista los contratos activos por fecha de expiración, marcando en rojo los que
vencen dentro de los próximos N días. Sale con código 1 si hay alguno (útil para cron).

---

## Manual DB backup

```powershell
python scripts/backup_db.py
```

Crea `backups/ntexecg_{YYYYMMDD_HHMMSS}.sql.gz` con `pg_dump` (requiere las
PostgreSQL client tools en PATH).

---

## LuxAlgo webhook URL format

```text
https://ntexecg.lipatolicucho.com/webhooks/luxalgo/{strategy_id}?token={secret}
```

- `strategy_id` **siempre** viene del path de la URL, nunca del payload.
- El `ticker` se configura manualmente en LuxAlgo; NTEXECG nunca lo transforma.

### LuxAlgo JSON payload (configurar manualmente en la alerta)

```json
{
    "ticker":    "MES",
    "action":    "[[strategy_order_action]]",
    "sentiment": "[[strategy_market_position]]",
    "quantity":  "1",
    "price":     "[[strategy_order_price]]",
    "time":      "[[timenow]]",
    "interval":  "[[timeframe]]"
}
```

El valor exacto de `"ticker"` para cada activo se muestra en la UI al crear la
estrategia (Symbol Mapper → columna *Pine Script Config*). Ej: Micro Yen = `"MJY"`
(no `"M6J"` — ese símbolo no existe en CME).

---

## Pipeline de 5 niveles (fail-fast)

1. **Sistema** — modo global, status de estrategia, symbol mapeado, bridge activo
2. **Temporal** — día de la semana y horario de sesión del activo
3. **Riesgo** — daily loss stop, max posiciones, position state
4. **Score** — QualityScorer (placeholder=100 en Fase 1, filtros en Fase 5)
5. **SL/TP** — SL **obligatorio** por ATR. Sin ATR → BLOCK. Las entradas nunca
   se aprueban sin `sl_price`.

Las salidas (exits) tienen prioridad: se permiten aunque el bridge esté inactivo
y saltan los niveles 3-5.

---

## Project structure

```text
app/
  api/            Webhook endpoint + health
  core/           Config, security, logging, scheduler
  db/             Base, session, migrations (Alembic)
  models/         SQLAlchemy ORM (17 tablas)
  services/       Symbol mapper, normalizer, pipeline, SL/TP, dispatch, …
  web/            Rutas UI (Jinja2 + HTMX)
  templates/      Plantillas HTML (Tailwind CDN + Alpine.js)
scripts/          Utilidades (seed, simulate_webhook, rollover, backup, mount)
tests/            pytest (SQLite en memoria, MockMarketDataProvider)
```
