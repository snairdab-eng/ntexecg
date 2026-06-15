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

## 3. Estructura de carpetas completa

```text
ntexecg/
│
├── app/
│   ├── main.py                              # FastAPI app factory + lifespan
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py                        # GET /health
│   │   ├── webhooks_luxalgo.py              # POST /webhooks/luxalgo/{strategy_id}
│   │   ├── webhooks_tradingview.py          # Futuro
│   │   └── internal/
│   │       ├── __init__.py
│   │       ├── strategies.py               # REST API estrategias
│   │       ├── signals.py                  # REST API señales
│   │       ├── positions.py                # REST API posiciones
│   │       ├── assets.py                   # REST API asset profiles
│   │       ├── symbol_map.py               # REST API symbol mapper
│   │       ├── settings.py                 # REST API settings
│   │       └── actions.py                  # flatten, pause, resume, etc.
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py                       # Pydantic Settings (env vars)
│   │   ├── security.py                     # hash_token, verify_token
│   │   ├── timezones.py                    # utilidades de timezone
│   │   ├── logging.py                      # configuración loguru
│   │   └── scheduler.py                    # APScheduler (heartbeat, cron)
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py                         # Base declarativa SQLAlchemy
│   │   ├── session.py                      # Async session factory
│   │   └── migrations/                     # Alembic
│   │       ├── env.py
│   │       ├── script.py.mako
│   │       └── versions/
│   │
│   ├── models/
│   │   ├── __init__.py
│   │   ├── raw_signal.py
│   │   ├── normalized_signal.py
│   │   ├── strategy.py
│   │   ├── strategy_profile.py
│   │   ├── asset_profile.py
│   │   ├── global_profile.py
│   │   ├── symbol_map.py
│   │   ├── decision.py
│   │   ├── position_state.py
│   │   ├── webhook_delivery.py
│   │   ├── conflict_log.py
│   │   ├── audit_log.py
│   │   ├── strategy_performance.py
│   │   ├── strategy_template.py
│   │   ├── market_data_status.py
│   │   ├── economic_event.py
│   │   └── ohlcv_bar.py                    # Fase 5
│   │
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── webhooks.py
│   │   ├── strategies.py
│   │   ├── signals.py
│   │   ├── decisions.py
│   │   ├── positions.py
│   │   ├── symbol_map.py
│   │   ├── assets.py
│   │   ├── templates.py
│   │   └── settings.py
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   │
│   │   # Recepción y normalización
│   │   ├── signal_normalizer.py
│   │   ├── symbol_mapper.py                # Búsqueda directa, sin prefijos
│   │   ├── deduplicator.py
│   │   │
│   │   # Configuración
│   │   ├── config_resolver.py              # Herencia global→asset→strategy
│   │   ├── strategy_registry.py
│   │   │
│   │   # Pipeline de filtros
│   │   ├── filter_pipeline.py              # Orquesta 5 niveles (fail-fast)
│   │   ├── session_validator.py            # Nivel 2: horario por activo
│   │   ├── news_filter.py                  # Nivel 2: noticias
│   │   ├── quality_scorer.py               # Nivel 4: score (placeholder Fase 1)
│   │   ├── sl_tp_calculator.py             # Nivel 5: SL obligatorio por ATR
│   │   │
│   │   # Datos de mercado
│   │   ├── market_data_service.py          # Abstracción + providers
│   │   │   # NinjaTraderBridgeProvider (producción)
│   │   │   # YfinanceProvider (desarrollo)
│   │   │   # TradovateAPIProvider (stub, Fase 5+)
│   │   │   # DatabentoProvider (stub, Fase 5+)
│   │   │
│   │   # Dispatch
│   │   ├── traderspost_client.py           # Cliente HTTP hacia TradersPost
│   │   ├── payload_builder.py              # Construir payload con SL
│   │   │
│   │   # Estado y métricas
│   │   ├── position_service.py
│   │   ├── performance_tracker.py
│   │   │
│   │   # Fases futuras (stubs en Fase 1)
│   │   ├── signal_conflict_resolver.py     # Fase 7
│   │   ├── account_risk_engine.py          # Fase 7
│   │   ├── portfolio_risk_engine.py        # Fase 7
│   │   ├── exit_manager.py                 # Fase 4
│   │   ├── hmm_service.py                  # Fase 6 (stub: retorna "unknown")
│   │   │
│   │   └── audit_service.py
│   │
│   ├── web/
│   │   ├── __init__.py
│   │   ├── routes_dashboard.py
│   │   ├── routes_strategies.py
│   │   ├── routes_signals.py
│   │   ├── routes_positions.py
│   │   ├── routes_symbol_map.py
│   │   ├── routes_assets.py
│   │   ├── routes_strategy_templates.py
│   │   ├── routes_settings.py
│   │   └── routes_audit.py
│   │
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── strategies.html
│   │   ├── strategy_detail.html
│   │   ├── strategy_form.html
│   │   ├── strategy_clone_form.html
│   │   ├── signals.html
│   │   ├── signal_detail.html
│   │   ├── positions.html
│   │   ├── symbol_map.html
│   │   ├── assets.html
│   │   ├── asset_form.html
│   │   ├── strategy_templates.html
│   │   ├── strategy_template_form.html
│   │   ├── settings.html
│   │   ├── audit.html
│   │   └── partials/
│   │       ├── events_feed.html
│   │       ├── bridge_status.html
│   │       ├── pipeline_breakdown.html
│   │       ├── performance_comparison.html
│   │       ├── strategy_row.html
│   │       ├── signal_row.html
│   │       ├── position_row.html
│   │       └── alert_banner.html
│   │
│   ├── static/
│   │   ├── css/app.css
│   │   └── js/app.js
│   │
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py                     # Fixtures: DB SQLite, MockProvider
│       ├── fixtures/
│       │   └── bridge/                     # JSON de ejemplo para tests del bridge
│       │       ├── bars_MES_5m.json
│       │       └── heartbeat_MES.json
│       ├── test_health.py
│       ├── test_webhooks_luxalgo.py
│       ├── test_normalizer.py
│       ├── test_symbol_mapper.py
│       ├── test_config_resolver.py
│       ├── test_filter_pipeline.py
│       ├── test_session_validator.py
│       ├── test_news_filter.py
│       ├── test_sl_tp_calculator.py
│       ├── test_market_data_service.py
│       ├── test_payload_builder.py
│       ├── test_dispatcher.py
│       ├── test_position_service.py
│       ├── test_performance_tracker.py
│       ├── test_audit.py
│       └── test_ui.py
│
├── docs/                                   # Documentación del proyecto
│   ├── 00_CONTRATO_TECNICO_v1_0.md
│   ├── 01_REQUERIMIENTOS_ACCIONABLES_v1_0.md
│   ├── 02_REQUERIMIENTOS_INTERFACE_WEB_v1_0.md
│   ├── 03_ARQUITECTURA_ESTRUCTURA_v1_0.md
│   ├── 04_MODELO_DATOS_v1_0.md
│   ├── 05_BACKLOG_ROADMAP_v1_0.md
│   ├── 06_PROMPTS_CLAUDE_CODE_v1_0.md
│   └── 07_INFRAESTRUCTURA_ENTORNOS_v1_0.md
│
├── scripts/
│   ├── seed_dev_data.py
│   ├── simulate_webhook.py
│   ├── rollover_alert.py
│   ├── backup_db.py
│   └── mount_ntbridge.sh                   # Montar \\NTRADER\bridge en Ubuntu
│
├── nginx/
│   └── nginx.conf
│
├── docker-compose.yml                      # Producción (NTEXECG)
├── docker-compose.dev.yml                  # Desarrollo (NTDEV)
├── Dockerfile
├── .env.example
├── .gitattributes                          # Forzar LF
├── pyproject.toml
├── alembic.ini
└── README.md
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

4. QualityScorer en Fase 1: retorna score=100 siempre.
   HMMService en Fase 1: retorna "unknown" siempre.
   Stubs explícitos con docstring indicando la fase de implementación.

5. MarketDataService se inyecta en startup, no se instancia en servicios.
   El provider se selecciona según MARKET_DATA_PROVIDER en .env.

6. NinjaTraderBridgeProvider verifica mtime del heartbeat, no el contenido.
   if (datetime.now() - datetime.fromtimestamp(file.stat().st_mtime)).seconds > max_age:
       return False  # NT inactivo

7. En NTEXECG producción: MARKET_DATA_PROVIDER=ninja_trader_bridge
   En NTDEV desarrollo:   MARKET_DATA_PROVIDER=yfinance
   No hardcodear el provider en el código.
```
