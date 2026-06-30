# INVENTARIO — c:\NTEXECG

> Generado: 2026-06-19 · **Solo lectura** (no se modificó ningún archivo del proyecto;
> esta es la única escritura). `alembic current` contra la BD **no** se ejecutó para no
> abrir conexión ni efectos; el estado de migraciones se deriva de los scripts en disco.

## 0. Panorama — conviven DOS proyectos distintos

| | **Proyecto A (existente)** | **Proyecto B (nuevo)** |
|---|---|---|
| Carpeta | `c:\NTEXECG\app` (+ raíz) | `c:\NTEXECG\ntexecg` |
| Qué es | Gateway LuxAlgo→TradersPost, pipeline de **5 niveles** + SL por ATR | Compuerta `/hook/{token}` con pipeline de **10 pasos** (build prompt) |
| Stack | FastAPI + SQLAlchemy + **PostgreSQL** + Alembic + Jinja/HTMX UI | FastAPI + Pydantic + **SQLite** tras interfaz de repo |
| Persistencia | 17 tablas ORM, migraciones Alembic | 3 tablas SQLite (sin Alembic) |
| Origen | trabajo previo (versionado en git) | generado hoy desde `PROMPTS/NTEXECG_build_prompt.md` |

Son independientes y no comparten código. El B se construyó en subcarpeta para **no
sobrescribir** el A (ver `PROMPTS/REPORTE_SALIDA.md`).

## 1. Árbol de carpetas (excluye .venv, .git, node_modules, __pycache__, .pytest_cache)

```
c:\NTEXECG
├─ app/                     # Proyecto A (SQLAlchemy/Postgres)
│  ├─ api/                  #   webhook + auth + health
│  │  └─ internal/
│  ├─ core/                 #   config, auth, security, logging, scheduler
│  ├─ db/
│  │  └─ migrations/
│  │     └─ versions/       #   2 migraciones Alembic
│  ├─ models/               #   17 modelos ORM
│  ├─ schemas/
│  ├─ services/             #   pipeline, symbol mapper, SL/TP, traderspost…
│  ├─ static/ (css, js)
│  ├─ templates/ (partials) #   UI Jinja2 + HTMX
│  └─ web/                  #   rutas UI
├─ ntexecg/                 # Proyecto B (nuevo, SQLite)
│  ├─ app/ (db, models, pipeline, services)
│  ├─ data/ (schemas)
│  └─ tests/
├─ scripts/                 # seed, simulate_webhook, rollover, backup, mount…
├─ tests/                   # pytest del proyecto A
├─ nginx/                   # config reverse proxy
├─ logs/
├─ CONTRATO/                # 00..07 contrato técnico + diseño
├─ DOCS/                    # diseño, manuales TradersPost/LuxAlgo (pdf/svg/docx)
├─ REPORTES/                # MEMORIA_TECNICA_NTDEV.md / _NTEXECG.md
├─ FASES_ANTERIORES/        # material previo (machotes, correcciones)
├─ PROMPTS/                 # build prompt + schemas + REPORTE_SALIDA + este inventario
├─ A REVISAR/               # copia de DOCS/FASES_ANTERIORES/PROMPTS + LEEME.txt
└─ (raíz) README.md, LEEME.txt, VALIDACION_LIMITES.md, alembic.ini,
          pyproject.toml, alias.bundle, ttfix.bundle
```

## 2. Modelos / tablas (Proyecto A — `app/models/`) y estado de Alembic

**17 modelos ORM SQLAlchemy → 17 tablas:**

| Archivo | Tabla |
|---|---|
| asset_profile.py | `asset_profiles` |
| audit_log.py | `audit_logs` |
| conflict_log.py | `conflict_logs` |
| decision.py | `strategy_decisions` |
| economic_event.py | `economic_events` |
| global_profile.py | `global_profile` |
| market_data_status.py | `market_data_status` |
| normalized_signal.py | `normalized_signals` |
| ohlcv_bar.py | `ohlcv_bars` |
| position_state.py | `position_states` |
| raw_signal.py | `raw_signals` |
| strategy.py | `strategies` |
| strategy_performance.py | `strategy_performance` |
| strategy_profile.py | `strategy_profiles` |
| strategy_template.py | `strategy_templates` |
| symbol_map.py | `symbol_maps` |
| webhook_delivery.py | `webhook_deliveries` |

**Migraciones Alembic (`app/db/migrations/versions/`):** 2 scripts, cadena lineal.

| Orden | Revisión | down_revision | Descripción |
|---|---|---|---|
| 1 (base) | `d14b378f0c8d` | `None` | initial_schema (2026-06-15) |
| 2 (**head**) | `a1029493ee79` | `d14b378f0c8d` | add_market_data_symbol_to_symbol_map (2026-06-17) |

- **Head en disco:** `a1029493ee79` (ninguna revisión lo referencia como down_revision).
- `env.py` es async; `script_location = app/db/migrations` (en `alembic.ini`).
- El **estado aplicado** (`alembic_version` en la BD) no se consultó para no conectar a
  PostgreSQL. Para verificarlo sin modificar: `alembic current` / `alembic heads`.

**Proyecto B (`ntexecg/`):** sin Alembic. Persistencia SQLite con 3 tablas creadas en
código (`SqliteRepository`): `strategies`, `accounts`, `audit_log`. Modelos = Pydantic
(`signal`, `strategy`, `account`, `instrument`, `audit`), no ORM.

## 3. Endpoints / rutas existentes

### Proyecto A — `app/` (FastAPI, título = `settings.APP_NAME`)

**Públicas / auth (sin protección de sesión):**
- `POST /webhooks/luxalgo/{strategy_id}` — recepción de señales LuxAlgo (`?token=`)
- `GET /health`
- `GET /ui/login` · `POST /ui/login` · `POST /ui/logout`

**UI protegida (incluida con `dependencies=protected`):**
- `GET /ui` (dashboard) · `GET /ui/partials/bridge-status` · `…/bridge-badge` · `…/recent-signals`
- `GET /ui/strategies` · `…/ticker-hint` · `GET|POST /ui/strategies/new` · `GET /ui/strategies/{id}`
  · `POST /ui/strategies/{id}/status` · `GET|POST /ui/strategies/{id}/clone` · `POST /ui/strategies/batch-action`
- `GET /ui/signals` · `GET /ui/signals/{id}`
- `GET /ui/positions` · `POST /ui/positions/{id}/flatten|lock|unlock`
- `GET /ui/symbol-map` · `POST /ui/symbol-map/new` · `POST /ui/symbol-map/{id}/toggle`
- `GET /ui/assets` · `GET|POST /ui/assets/{symbol}`
- `GET|POST /ui/strategy-templates` (`/new`)
- `GET|POST /ui/settings`
- `GET /ui/audit`

### Proyecto B — `ntexecg/app/main.py`

- `POST /hook/{token}` — compuerta de validación (10 pasos)
- `GET /catalog`
- `POST /admin/strategies/validate` · `POST|GET /admin/strategies` · `GET|PUT /admin/strategies/{id}`
  · `POST /admin/strategies/{id}/enable|disable`
- `POST|GET /admin/accounts` · `GET|PUT /admin/accounts/{id}` · `GET /admin/accounts/{id}/risk`
- `GET /admin/audit`
- (FastAPI expone además `/docs`, `/openapi.json`)

## 4. Estado actual según README / docs

**`README.md` (raíz, Proyecto A) — "NTEXECG — Signal Gateway":**
- Propósito: recibe señales LuxAlgo, las evalúa con **pipeline de 5 niveles (fail-fast)**,
  agrega **Stop Loss obligatorio por ATR**, y reenvía las aprobadas a TradersPost. *No*
  genera señales ni administra portafolio.
- Pipeline 5 niveles: 1) Sistema 2) Temporal 3) Riesgo 4) Score (placeholder=100 en Fase 1)
  5) SL/TP por ATR (sin ATR → BLOCK; las entradas nunca se aprueban sin `sl_price`).
  Las salidas tienen prioridad (saltan niveles 3–5, permitidas con bridge inactivo).
- Entornos: **NTDEV** (Windows, Postgres local, `MARKET_DATA_PROVIDER=yfinance`),
  **NTEXECG** prod (Ubuntu/Docker/Nginx, `ninja_trader_bridge`, monta `\\NTRADER\bridge`),
  **NTRADER** (NinjaTrader + `NTraderExecutionBridge.cs`).
- **Modo DRY RUN por defecto** (`DRY_RUN=true`): aprobadas **no** se envían a TradersPost
  (se registra `WebhookDelivery` con status `DRY_RUN`). Envío real requiere `DRY_RUN=false`
  + `TRADERSPOST_ENABLED=true`.
- Setup: venv, `pip install -e ".[dev]"`, `createdb`, `alembic upgrade head`,
  `seed_dev_data.py`, `uvicorn app.main:app`. Tests: SQLite en memoria + `MockMarketDataProvider`.
- URL webhook: `https://.../webhooks/luxalgo/{strategy_id}?token={secret}`; `strategy_id`
  siempre del path; `ticker` se configura en LuxAlgo y nunca se transforma.

**`LEEME.txt` (raíz):** "PAQUETE NTEXECG — 19/06/2026". Describe el paquete de entrega
(`/PROMPTS`, `/DOCS`, `/FASES_ANTERIORES`) — es material de contexto/handoff, no estado de runtime.

**Documentación de diseño / estado (no leída en detalle aquí, referenciada):**
- `CONTRATO/00..07_*.md` — contrato técnico, requerimientos, arquitectura, **modelo de datos**,
  backlog/roadmap, prompts, infraestructura.
- `REPORTES/MEMORIA_TECNICA_NTEXECG.md` y `MEMORIA_TECNICA_NTDEV.md` — memorias técnicas.
- `DOCS/NTEXECG_diseno_continuacion_con_anexo.(md/pdf)` + `NTEXECG_anexo_B_flujo.svg` — diseño + Anexo A/B.
- `PROMPTS/REPORTE_SALIDA.md` — reporte del Proyecto B (nuevo) construido hoy.
- `VALIDACION_LIMITES.md` (raíz) — validación de límites de riesgo.

## 5. Notas

- `alias.bundle` y `ttfix.bundle` (raíz) son **git bundles** (~1.9 MB c/u) de trabajo previo,
  no parte del runtime.
- Hay una carpeta **`A REVISAR/`** que duplica `DOCS/`, `FASES_ANTERIORES/`, `PROMPTS/` y el
  `LEEME.txt` — posible material pendiente de consolidar.
- No se ejecutó ninguna app, test, ni comando de BD/git para este inventario.
