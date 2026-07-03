# NTEXECG — Arquitectura y Estructura de Proyecto v1.0

---

## 1. Arquitectura de la infraestructura completa

```text
┌──────────────────────────────────────────────────────────────────────┐
│                         RED LOCAL (LAN)                             │
│                                                                      │
│  ┌────────────────────────────┐    ┌──────────────────────────────┐  │
│  │          NTRADER           │    │          NTEXECG             │  │
│  │    Windows Server 2025     │    │    Ubuntu Server 24.04       │  │
│  │                            │    │                              │  │
│  │  NinjaTrader Desktop       │    │  ┌──────────────────────┐   │  │
│  │  (Tradovate feed — CME)    │    │  │  Docker Compose      │   │  │
│  │                            │    │  │                      │   │  │
│  │  Charts activos:           │    │  │  ┌────────────────┐  │   │  │
│  │  MES 5m + bridge ──────────┼────┼──▶│ NTEXECG App    │  │   │  │
│  │  MNQ 5m + bridge           │    │  │  │ (FastAPI)      │  │   │  │
│  │  MJY 5m + bridge           │Samba  │  │ /mnt/ntbridge  │  │   │  │
│  │  MGC 5m + bridge           │    │  │  └───────┬────────┘  │   │  │
│  │         ↓ cada 10s         │    │  │          │           │   │  │
│  │  C:\NTraderSystem\         │    │  │  ┌───────┴────────┐  │   │  │
│  │  bridge\out\               │    │  │  │  PostgreSQL    │  │   │  │
│  │  \\NTRADER\bridge ─────────┼────┼──▶  └────────────────┘  │   │  │
│  └────────────────────────────┘    │  └──────────────────────┘   │  │
│                                    │  Nginx + HTTPS               │  │
│                                    │  ← webhooks LuxAlgo          │  │
│                                    │  → señales a TradersPost     │  │
│                                    └──────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
                               │ VPN (sitio remoto)
              ┌────────────────┴──────────────────┐
              │              NTDEV                │
              │       Windows Server 2025         │
              │  VS Code + Claude Code (Max 5x)   │
              │  Docker Desktop (dev local)        │
              │  MARKET_DATA_PROVIDER=yfinance     │
              │  Accede via VPN a NTRADER/NTEXECG  │
              └───────────────────────────────────┘
```

---

## 2. Flujo de procesamiento de una señal

> **Actualización 2026-07-03 (NX-25):** el diagrama de abajo es el diseño
> original. Diferencias con el código real: el **dedupe** corre en
> `process_signal` ANTES del pipeline (no es el check 1.3); **no existe** el
> check "1.5 allowed_symbols"; el filtro de **noticias (2.3) es stub**; L3
> incluye **3.4 symbol_busy** (NX-09); L4 emite **quality UNKNOWN/LOW/MEDIUM/
> HIGH** (NX-04); L5 usa `atr_timeframe` calibrado (NX-14) y bloquea sin precio
> (NX-05); el dispatch es **multi-perfil** con kill-switch por capas.

```text
POST /webhooks/luxalgo/{strategy_id}?token={secret}
  payload: {"ticker": "MJY", "action": "sell", "sentiment": "short", ...}
    │
    ├── Validar token (security.py)
    ├── Guardar RawSignal (siempre)
    ├── Responder 200 inmediatamente
    └── Background task: process_signal()
                │
                ▼
        SignalNormalizer
        ├── strategy_id del URL path
        ├── ticker_received = "MJY" (exactamente como llegó)
        ├── SymbolMapper: "MJY" → "MJYU2025"
        │   (búsqueda directa, sin lógica de strings)
        ├── sentiment="flat" → action="exit"
        ├── Castear tipos, normalizar timeframe
        └── Crear NormalizedSignal
                │
                ▼
        Deduplicator → IGNORE_DUPLICATE si hash existe en 60s
                │
                ▼
        StrategyRegistry → QUEUE_FOR_REVIEW si candidate
                │
                ▼
        ConfigResolver: GlobalProfile → AssetProfile → StrategyProfile
                │
                ▼
        FilterPipeline.evaluate()
        │
        ├─ NIVEL 1: Validación del sistema (6 checks)
        │  1.1 global_mode  1.2 strategy_status  1.3 dedupe
        │  1.4 symbol_map   1.5 allowed_symbols   1.6 bridge_active
        │  → Falla: BLOCK inmediato
        │
        ├─ NIVEL 2: Contexto temporal
        │  2.1 día semana (por activo)  2.2 horario sesión  2.3 noticias
        │  → Falla entrada: BLOCK
        │  → Falla salida: evaluar allow_exits_outside
        │
        ├─ NIVEL 3: Riesgo
        │  3.1 daily_loss_stop  3.2 max_positions  3.3 position_state
        │  → Falla: BLOCK
        │
        ├─ NIVEL 4: Score (solo entradas)
        │  QualityScorer (placeholder=100 en Fase 1)
        │  → score < min: BLOCK
        │
        └─ NIVEL 5: SL/TP (solo entradas aprobadas)
           SLTPCalculator → MarketDataService.get_atr()
           → ATR no disponible: BLOCK
           → Calcular sl_price (OBLIGATORIO)
                │
                ▼
        StrategyDecision guardada (siempre)
                │
                ▼ (si APPROVE)
        PayloadBuilder → payload con stopLoss
                │
                ▼
        TradersPostClient → POST httpx (o DRY_RUN)
                │
                ▼
        WebhookDelivery + PositionService + PerformanceTracker
```

---

## 3. Estructura de carpetas (ACTUALIZADA 2026-07-03 — árbol real)

> NX-25: el árbol original (build prompt) quedó archivado en `A REVISAR/`.
> Diferencias clave vs el diseño: `app/schemas/` y `app/api/internal/` quedaron
> vacíos (la validación vive en las rutas/Pydantic inline); no existen
> `news_filter.py` (stub L2.3 dentro del pipeline), `strategy_registry.py`
> (lógica en L1.2) ni `timezones.py`; los tests viven en `tests/` (raíz).

```text
NTEXECG/
├── app/
│   ├── main.py                    # app factory + lifespan (scheduler jobs)
│   ├── api/
│   │   ├── health.py              # GET /health
│   │   ├── auth_routes.py         # login/logout UI
│   │   └── webhooks_luxalgo.py    # webhook + process_signal + dispatch multi-perfil
│   ├── core/
│   │   ├── config.py              # Settings (env)
│   │   ├── security.py            # hash/verify de tokens de webhook (NX-22)
│   │   ├── auth.py / auth_middleware.py
│   │   ├── logging.py
│   │   └── scheduler.py           # Heartbeat, ExitManager (+stale/reservas), Bars, HMM
│   ├── db/                        # base, session, migrations/ (Alembic)
│   ├── models/                    # strategy, strategy_profile, asset_profile,
│   │                              # global_profile, symbol_map, raw/normalized_signal,
│   │                              # decision, position_state, webhook_delivery,
│   │                              # audit_log, conflict_log (reservado NX-18C),
│   │                              # strategy_performance, strategy_template,
│   │                              # market_data_status, ohlcv_bar, execution_result
│   ├── services/
│   │   ├── signal_normalizer.py   # + time real del payload (NX-16)
│   │   ├── symbol_mapper.py / deduplicator.py
│   │   ├── config_resolver.py     # global < asset < strategy (kill-switch OR/AND)
│   │   ├── filter_pipeline.py     # 5 niveles + symbol_busy (NX-09) + quality (NX-04)
│   │   ├── session_validator.py / quality_scorer.py / sl_tp_calculator.py
│   │   ├── hmm_service.py / hmm_trainer.py / regime_features.py
│   │   ├── market_data_service.py / bar_store.py
│   │   ├── payload_builder.py     # + build_scaled (multi-leg)
│   │   ├── dispatch_profiles.py   # perfiles de riesgo (tiers)
│   │   ├── traderspost_client.py  # retries configurables (NX-15)
│   │   ├── position_service.py / exit_manager.py / forced_exit.py
│   │   ├── results_import.py      # Fase 8 + reconciliación Fase A (NX-18)
│   │   ├── strategy_aliases.py    # alias de renames para Analytics (NX-24)
│   │   ├── performance_tracker.py / audit_service.py / repositories.py
│   ├── web/                       # routes_{dashboard,strategies,signals,analytics,
│   │                              # positions,assets,symbol_map,api,strategy_templates,
│   │                              # settings,audit}.py + common.py
│   ├── templates/                 # base, dashboard, strategies, strategy_detail,
│   │                              # signals, signal_detail, analytics, positions,
│   │                              # assets, settings, audit, partials/
│   └── static/
├── tests/                         # suite completa (pytest, SQLite in-memory)
├── scripts/                       # calibración/diagnóstico/estudios (dry-run+backup+audit)
├── alembic.ini · pyproject.toml · docker-compose*.yml · nginx/
└── CONTRATO/ · DOCS/ · REPORTES/ · NINJATRADER/ · A REVISAR/ (archivo histórico)
```

---

## 4. Docker Compose

### docker-compose.yml (Producción — NTEXECG Ubuntu)

```yaml
version: "3.9"

services:
  app:
    build: .
    restart: always
    env_file: .env
    expose:
      - "8000"
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - app_logs:/app/logs
      - /mnt/ntbridge:/mnt/ntbridge:ro
      # /mnt/ntbridge montado en Ubuntu host desde \\NTRADER\bridge (Samba)

  db:
    image: postgres:16-alpine
    restart: always
    env_file: .env
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5

  proxy:
    image: nginx:alpine
    restart: always
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - app

volumes:
  postgres_data:
  app_logs:
```

### docker-compose.dev.yml (Desarrollo — NTDEV Windows)

```yaml
version: "3.9"
# NTDEV está en sitio remoto via VPN.
# NO monta \\NTRADER\bridge (inestable via VPN).
# Usa YfinanceProvider (delayed ~15min, suficiente para desarrollo).

services:
  app:
    build: .
    restart: unless-stopped
    env_file: .env
    ports:
      - "8000:8000"
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - .:/app
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
    environment:
      - MARKET_DATA_PROVIDER=yfinance

  db:
    image: postgres:16-alpine
    restart: unless-stopped
    env_file: .env
    ports:
      - "5432:5432"
    volumes:
      - postgres_data_dev:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres_data_dev:
```

---

## 5. pyproject.toml (dependencias)

```toml
[project]
name = "ntexecg"
version = "1.0.0"
requires-python = ">=3.12"

dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.30.0",
    "pydantic>=2.7.0",
    "pydantic-settings>=2.3.0",
    "sqlalchemy>=2.0.30",
    "alembic>=1.13.0",
    "asyncpg>=0.29.0",
    "httpx>=0.27.0",
    "jinja2>=3.1.4",
    "python-multipart>=0.0.9",
    "loguru>=0.7.2",
    "apscheduler>=3.10.4",
    "python-dateutil>=2.9.0",
    "pytz>=2024.1",
    "yfinance>=0.2.40",
    "pandas>=2.2.0",
    "pandas-ta>=0.3.14b",
    "beautifulsoup4>=4.12.0",
    "requests>=2.32.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0",
    "pytest-cov>=5.0.0",
    "aiosqlite>=0.20.0",
]
phase6 = [
    "hmmlearn>=0.3.0",
    "numpy>=1.26.0",
    "scikit-learn>=1.4.0",
]
```

---

## 6. .gitattributes

```text
* text=auto eol=lf
*.py text eol=lf
*.md text eol=lf
*.html text eol=lf
*.yaml text eol=lf
*.yml text eol=lf
*.toml text eol=lf
*.sh text eol=lf
*.png binary
*.jpg binary
*.ico binary
```

---

## 7. Separación de responsabilidades (regla estricta)

```text
templates/      → Solo HTML. Cero lógica de trading.
web/routes_*    → Recibir request, llamar servicio, retornar template.
services/       → Toda la lógica de negocio. Sin dependencia de templates.
api/            → Recibir webhook/request REST, delegar a services.
models/         → Solo definición de tablas SQLAlchemy.
schemas/        → Solo validación Pydantic.
core/           → Config, seguridad, logging, scheduler.

Un servicio nunca importa un template.
Un template nunca contiene lógica de decisión.
MarketDataService es la única puerta a datos de mercado.
```

---

## 8. Notas críticas para Claude Code

```text
1. ticker_received = exactamente payload["ticker"], sin modificar.
   Symbol Mapper hace búsqueda directa: WHERE tv_symbol = ticker_received.
   PROHIBIDO: lógica de strings, prefijos, transformaciones en el ticker.

2. SLTPCalculator nunca retorna sl_price=None con passed=True.
   Si ATR no disponible: passed=False, reason="atr_calculation_failed".

3. Tests: SIEMPRE MockMarketDataProvider. Nunca yfinance real ni bridge real.
   class MockMarketDataProvider(MarketDataProvider):
       async def get_atr(self, ...): return 8.0
       async def is_active(self, ...): return True

4. [OBSOLETO 2026-07-03] QualityScorer/HMM ya están implementados (Fase 5/6):
   score ponderado 0-100 con etiqueta de calidad (UNKNOWN sin filtros, NX-04)
   y régimen Kaufman ER / HMM entrenado.

5. MarketDataService se inyecta en startup, no se instancia en servicios.
   El provider se selecciona según MARKET_DATA_PROVIDER en .env.

6. NinjaTraderBridgeProvider verifica mtime del heartbeat, no el contenido.
   if (datetime.now() - datetime.fromtimestamp(file.stat().st_mtime)).seconds > max_age:
       return False  # NT inactivo

7. En NTEXECG producción: MARKET_DATA_PROVIDER=ninja_trader_bridge
   En NTDEV desarrollo:   MARKET_DATA_PROVIDER=yfinance
   No hardcodear el provider en el código.
```
