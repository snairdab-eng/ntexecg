# NTEXECG — Requerimientos Técnicos Accionables v1.0

---

## Instrucción general para Claude Code

```text
Construir NTEXECG de forma modular, testeable y con interfaz web desde el inicio.
Toda configuración operativa vive en base de datos y se administra desde UI.

REGLAS CRÍTICAS:
1. strategy_id SIEMPRE del URL path, NUNCA del payload.
2. ticker llega EXACTAMENTE como el operador lo configuró en LuxAlgo.
   NTEXECG nunca transforma ni infiere el ticker.
   Symbol Mapper hace búsqueda directa sin lógica de strings.
3. sentiment="flat" SIEMPRE produce action="exit".
4. Toda entrada APROBADA DEBE tener sl_price calculado.
   Si ATR no disponible → BLOCK (nunca aprobar sin SL).
5. Filtros se evalúan en orden estricto (5 niveles, fail-fast).
6. El operador toma TODAS las decisiones de ciclo de vida.
   NTEXECG nunca degrada estrategias automáticamente.
7. Tests SIEMPRE usan MockMarketDataProvider.
   Nunca llamar yfinance ni leer archivos del bridge en tests.
8. Usar rutas con forward slash. Line endings LF.
9. La UI no contiene lógica de trading.
10. Los servicios no importan templates.
```

---

## Épica 0 — Inicialización del repositorio

### REQ-0001 — Estructura base del proyecto

```text
ntexecg/
├── app/
│   ├── main.py
│   ├── api/
│   │   ├── health.py
│   │   ├── webhooks_luxalgo.py
│   │   └── internal/ (strategies, signals, positions, assets, settings, actions)
│   ├── core/
│   │   ├── config.py        (Pydantic Settings)
│   │   ├── security.py      (hash_token, verify_token)
│   │   ├── timezones.py
│   │   ├── logging.py       (loguru)
│   │   └── scheduler.py     (APScheduler)
│   ├── db/
│   │   ├── base.py, session.py, migrations/
│   ├── models/              (ver doc 04 para lista completa)
│   ├── schemas/
│   ├── services/            (ver doc 03 para lista completa)
│   ├── web/                 (ver doc 02 para rutas)
│   ├── templates/           (Jinja2)
│   ├── static/              (Tailwind CSS, Alpine.js)
│   └── tests/
├── docs/
├── scripts/
│   ├── seed_dev_data.py
│   ├── simulate_webhook.py
│   ├── rollover_alert.py
│   ├── backup_db.py
│   └── mount_ntbridge.sh
├── docker-compose.yml       (NTEXECG — producción)
├── docker-compose.dev.yml   (NTDEV — desarrollo)
├── Dockerfile
├── .env.example
├── .gitattributes           (forzar LF)
├── pyproject.toml
├── alembic.ini
└── README.md
```

Criterio de aceptación:
```text
- docker compose -f docker-compose.dev.yml up -d sin errores
- GET /health → 200
- GET /ui → 200
- pytest corre sin errores
```

### REQ-0002 — Variables de entorno

```text
app/core/config.py con Pydantic Settings:

APP_ENV, APP_NAME, APP_VERSION
DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
SECRET_KEY, WEBHOOK_TOKEN_SALT, LUXALGO_WEBHOOK_SECRET
TRADERSPOST_ENABLED, DRY_RUN
DEFAULT_TIMEZONE, LOG_LEVEL
MAX_RETRY_ATTEMPTS, RETRY_BACKOFF_SECONDS
MARKET_DATA_PROVIDER        (ninja_trader_bridge | yfinance | tradovate | databento)
NTBRIDGE_PATH               (/mnt/ntbridge)
NTBRIDGE_HEARTBEAT_MAX_AGE  (60)
MARKET_DATA_FALLBACK_ENABLED (false)
NEWS_CACHE_TTL_MINUTES      (60)
```

---

## Épica 1 — Base de datos y migraciones

### REQ-0101 — PostgreSQL + SQLAlchemy async + Alembic

### REQ-0102 — Modelos iniciales

Ver documento 04 (Modelo de Datos v1.0) para definición completa.

Modelos a crear:
```text
RawSignal, NormalizedSignal, Strategy, GlobalProfile,
AssetProfile, StrategyProfile, SymbolMap, StrategyDecision,
PositionState, StrategyPerformance, StrategyTemplate,
WebhookDelivery, ConflictLog, AuditLog, GlobalSetting,
MarketDataStatus, EconomicEvent, OhlcvBar (vacío, Fase 5)
```

Seed (scripts/seed_dev_data.py):
```text
- GlobalProfile con safe defaults (dry_run=true, traderspost_enabled=false)
- SymbolMaps: MES, MNQ, MYM, M2K, MGC, MJY, M6E, 6J, 6E
  con pine_script_config y contract_type
- AssetProfiles con session_config_json para cada activo
- Un StrategyTemplate: "LuxAlgo Confirmation Normal"
- MarketDataStatus inicial para cada símbolo (is_active=false)
- GlobalSettings: market_data_provider, heartbeat_max_age, etc.
```

---

## Épica 2 — Interfaz web

### REQ-0201 — Layout base

```text
templates/base.html:
  Navbar con links: Dashboard, Estrategias, Señales, Posiciones,
                    Activos, Symbol Mapper, Templates, Settings, Audit
  Badge modo global: NORMAL(verde) / DEFENSIVE(amarillo) /
                     FLATTEN_ONLY(naranja) / PAUSED(rojo)
  Badge DRY RUN (naranja, visible cuando dry_run=true)
  Badge estado bridge: ● Activo / ⚠ Inactivo (HTMX polling 30s)
  Flash messages
  Footer con versión y entorno
```

### REQ-0202 — Dashboard (/ui)

```text
- Métricas de hoy: recibidas, aprobadas, bloqueadas, enviadas
- Estrategias: activas, en paper, en live, pausadas
- Estado del bridge por símbolo (ATR, heartbeat age)
- Alertas críticas: NT inactivo, contratos próximos a vencer, deliveries fallidos
- Feed de últimos 20 eventos (HTMX polling cada 10s)
- Acciones rápidas: [Pausar todo] [Flatten only] [Reanudar]
```

### REQ-0203 — Estrategias (/ui/strategies)

```text
Lista: strategy_id, nombre, activo, TF, status badge, modo, BT WR%, Real WR%
Acciones por fila: Ver, Editar, Clonar, Pausar/Reanudar
Selección múltiple + acciones en lote: Pausar, Shadow, Retirar
Botones: [+ Nueva estrategia] [+ Desde template]

Formulario de nueva estrategia (orden empatado con TradersPost):
  Sección 1: Nombre, descripción, fuente, activo base, TF, tipo
  → Al seleccionar activo: muestra ticker exacto para LuxAlgo
    ┌──────────────────────────────────────────────────┐
    │ Ticker para el JSON de LuxAlgo: "ticker": "MJY"│
    │ [📋 Copiar]                                      │
    └──────────────────────────────────────────────────┘
  Sección 2: Métricas BT (Win Rate, PF, Max DD, # trades, desde)
  Sección 3: TradersPost webhook URL, modo inicial
  Sección 4: [Generar token] → URL de NTEXECG para LuxAlgo
  Sección 5: Config inicial (todo heredado por default)
  Resumen + [Guardar]

Post-guardado: muestra ticker y URL copiables, instrucciones de 3 pasos

Perfil de estrategia (/ui/strategies/{id}) — tabs:
  General, Horario, Activos y cuentas, Filtros (pipeline),
  Exit Policy, Riesgo, Performance (BT vs Real), Señales,
  Decisiones, Auditoría

Acciones con confirmación en modal:
  Pausar, Reanudar, Mover a paper/micro/live (live requiere "CONFIRMAR"),
  Quarantine (motivo obligatorio), Retirar (motivo obligatorio), Clonar
```

### REQ-0204 — Señales (/ui/signals)

```text
Lista con filtros: strategy_id, symbol, action, decision, date range
Detalle (/ui/signals/{id}):
  - Raw payload JSON
  - Señal normalizada (ticker_received + symbol mapeado)
  - Desglose del pipeline por nivel (✅/❌/⏭ con detalle)
  - SL calculado (para APPROVE): entry, ATR, SL price, multiplier, proveedor
  - Payload enviado a TradersPost
  - Estado del delivery (DRY_RUN / SENT / FAILED)
```

### REQ-0205 — Symbol Mapper (/ui/symbol-map)

```text
Tabla con columnas prominentes: Pine Script Config | TV Symbol | Contrato | Tipo | Expira
Alerta cuando expiry <= 7 días
Nota: "El ticker en Pine Script Config es el valor exacto para LuxAlgo"
Acciones: Agregar, Editar, Desactivar (no borrar)
```

### REQ-0206 — Asset Profiles (/ui/assets)

```text
Lista: symbol, descripción, pine_script_config, sesión, SL mult
Formulario de edición:
  - pine_script_config (instrucción para LuxAlgo)
  - session_config_json (timezone, días, horarios, overnight)
  - sl_atr_multiplier, tp_atr_multiplier, atr_period
  - max_open_positions_symbol, daily_loss_stop
```

### REQ-0207 — Strategy Templates (/ui/strategy-templates)

```text
Lista de templates con # de estrategias derivadas
Formulario: nombre, tipo, config base, métricas típicas
[Crear estrategia desde template] → formulario precargado
```

### REQ-0208 — Posiciones (/ui/positions)

```text
Advertencia prominente: "Estado estimado — verificar en TradersPost/NinjaTrader"
Columnas: cuenta, símbolo, estado~, dirección, entry, SL enviado
Acciones: Flatten (con confirmación), Lock, Unlock
```

### REQ-0209 — Settings (/ui/settings)

```text
Sistema: global_mode, dry_run, traderspost_enabled, timezone
Riesgo global: max_positions, daily_loss_stop, daily_profit_lock
TradersPost: retry_attempts, timeout
Pipeline global: news_window_minutes, score_minimum
Datos de mercado: provider activo, path bridge, heartbeat_max_age
```

### REQ-0210 — Audit (/ui/audit)

```text
Tabla: fecha/hora, actor, acción, objeto, campo, valor anterior, nuevo, motivo
Filtros: actor, acción, objeto, date range
Exportar CSV
```

---

## Épica 3 — Webhook Receiver

### REQ-0301 — Endpoint LuxAlgo

```text
POST /webhooks/luxalgo/{strategy_id}?token={secret}

1. Validar token (hash contra DB)
2. Guardar RawSignal (siempre, incluso si token inválido)
3. Token inválido → 401 + AuditLog
4. Token válido → 200 inmediato: {"received": true, "signal_id": "uuid"}
5. Background task: process_signal()
```

### REQ-0302 — Signal Normalizer

```text
- strategy_id del URL path (NUNCA del payload)
- ticker_received = exactamente payload["ticker"] sin modificar
- SymbolMapper.map_symbol(db, ticker_received) → symbol (o None)
- sentiment="flat" → action="exit" (SIEMPRE)
- price: string → float
- quantity: string → int (default 1)
- time: ISO 8601 → datetime UTC
- interval: "5" → "5m", "60" → "1h", "D" → "1d", etc.
```

### REQ-0303 — Deduplicación

```text
SHA256( source + strategy_id + ticker_received + action + timestamp_truncado_minuto )
Si hash existe en últimos 60s → IGNORE_DUPLICATE
```

---

## Épica 4 — Strategy Registry

### REQ-0401 — Auto-creación de estrategia candidate

```text
Si strategy_id desconocido:
  Crear Strategy(status=candidate, enabled=false)
  Crear StrategyProfile con defaults de GlobalProfile
  AuditLog(actor="system", action="CREATE")
  Decisión: QUEUE_FOR_REVIEW
  Notificación en dashboard
```

### REQ-0402 — Comportamiento por estado

```text
candidate:    QUEUE_FOR_REVIEW, no dispatch
shadow:       Procesar y decidir, no dispatch, log completo
paper:        Dispatch a webhook paper de TradersPost
micro:        Dispatch con quantity=1 siempre
limited_live: Dispatch con límites diarios activos
live:         Dispatch completo según StrategyProfile
paused:       Entradas → BLOCK. Salidas → continuar
quarantined:  Todo → BLOCK, alerta en dashboard
retired:      Todo → BLOCK
```

---

## Épica 5 — Filter Pipeline + MarketDataService

### REQ-0501 — FilterPipeline (5 niveles, fail-fast)

Ver doc 00 sección 8 para lógica completa de cada nivel.

```python
class FilterPipeline:
    async def evaluate(db, signal, strategy, config) -> PipelineResult:
        # Nivel 1 → Nivel 2 → Nivel 3 → Nivel 4 → Nivel 5
        # Si cualquier nivel falla: retornar inmediatamente
        # Las salidas saltan Nivel 4 y van directo a Nivel 5
        # Nivel 5 no aplica a salidas (no necesitan SL)
```

### REQ-0502 — SessionValidator (Nivel 2.1 y 2.2)

```text
- Valida día y horario según session_config_json del AssetProfile
- Maneja sesiones 24h (next_day_end=true para MJY, 6J, 6E)
- Si fuera de ventana + salida + allow_exits=true → PERMIT
```

### REQ-0503 — NewsFilter (Nivel 2.3)

```text
- Cache de eventos económicos en tabla economic_events (TTL 1h)
- Fuente: ForexFactory scraping (MVP)
- Filtra por news_impact_levels_json (default: ["high"])
- Ventana: ±news_window_minutes alrededor del evento
```

### REQ-0504 — Config Resolver (herencia de 3 capas)

```text
1. Cargar GlobalProfile
2. Si AssetProfile para el símbolo: merge (asset sobreescribe global)
3. Si StrategyProfile: merge (strategy sobreescribe asset+global)
Solo los campos explícitamente definidos sobreescriben el nivel superior.
None = "no definido" = heredar del nivel superior.
```

### REQ-0505 — QualityScorer (Nivel 4 — placeholder en Fase 1)

```text
Fase 1: retorna score=100 siempre (estructura lista para Fase 5)
Fase 5: implementar volumen_relativo, atr_normalizado, vwap, time_of_day
Fase 6: agregar hmm_regime
```

### REQ-0506 — SLTPCalculator (Nivel 5 — OBLIGATORIO)

```text
1. Obtener ATR desde MarketDataService
2. Si ATR is None o <= 0 → BLOCK: atr_calculation_failed
3. LONG:  sl = entry - (ATR × sl_atr_multiplier)
   SHORT: sl = entry + (ATR × sl_atr_multiplier)
4. Validar: sl_distance_pct <= max_sl_pct (default 5%)
   Si excede → BLOCK: sl_too_wide
5. TP: None por defecto (via Builtin-Exits LuxAlgo)
   Si tp_atr_multiplier configurado → calcular TP

INVARIANTE: Nunca retornar passed=True con sl_price=None
```

### REQ-0507 — MarketDataService (abstracción)

```text
class MarketDataProvider(ABC):
    get_bars(symbol, timeframe, count) → list[dict] | None
    get_atr(symbol, timeframe, period) → float | None
    is_active(symbol) → bool

NinjaTraderBridgeProvider:
  Lee /mnt/ntbridge/bars_{symbol}_{tf}.json
  is_active() verifica mtime de heartbeat_{symbol}.json < max_age
  Producción (NTEXECG)

YfinanceProvider:
  Usa TICKER_MAP: {"MES": "MES=F", "MJY": "MJY=F", ...}
  is_active() siempre True en dev
  Desarrollo (NTDEV)

TradovateAPIProvider, DatabentoProvider:
  Stubs con NotImplementedError
  Para Fase 5+

Provider se selecciona en startup según MARKET_DATA_PROVIDER en .env
Tests: siempre usar MockMarketDataProvider
```

### REQ-0508 — HeartbeatMonitor (APScheduler)

```text
Tarea cada 30 segundos:
  Para cada símbolo en symbol_maps (active=true):
    - Verificar market_data.is_active(symbol)
    - Obtener ATR si activo
    - Actualizar market_data_status en DB
    - Si cambió a inactivo: alerta crítica en dashboard
```

---

## Épica 6 — TradersPost Dispatcher

### REQ-0601 — TradersPostClient

```text
- httpx async, timeout 10s
- Reintentos: máximo 3, backoff exponencial (1s, 2s, 4s)
- dry_run=true → WebhookDelivery(DRY_RUN), sin HTTP real
- Exit signals: reintentar hasta confirmar (hasta 10 intentos)
- Entry signals: no reintentar si > entry_signal_timeout_secs
- WebhookDelivery siempre registrado
- URL enmascarada en logs y DB
```

### REQ-0602 — PayloadBuilder

```text
Entradas: SIEMPRE incluir stopLoss:
{
  "ticker":      "MESU2025",     ← símbolo mapeado
  "action":      "sell",
  "sentiment":   "short",
  "signalPrice": 5500.00,
  "quantity":    1,
  "stopLoss":    {"type": "stop", "price": 5484.00},
  "extras":      {"strategy_id": "...", "ntexecg_score": 78, "atr": 8.0}
}

Salidas: sin stopLoss:
{
  "ticker":    "MESU2025",
  "action":    "exit",
  "sentiment": "flat"
}

Si pipeline_result.sl_price is None para una entrada → raise ValueError
(nunca debería ocurrir si el pipeline está correcto)
```

---

## Épica 7 — Position State

### REQ-0701 — PositionService

```text
Estados: FLAT, LONG, SHORT, PENDING_LONG, PENDING_SHORT,
         EXITING, REVERSING, LOCKED, UNKNOWN

on_entry_approved() → PENDING_LONG / PENDING_SHORT
on_delivery_confirmed() → LONG / SHORT / FLAT
on_exit_approved() → EXITING
on_flatten_manual() → EXITING + AuditLog
on_lock() → LOCKED + AuditLog

state_source="estimated" en MVP (basado en señales enviadas)
UI siempre indica que el estado es estimado
```

---

## Épica 8 — Audit Log

### REQ-0801 — AuditService

```text
Registro automático de:
  - Cambios de configuración desde UI
  - Cambios de status de estrategia
  - Acciones manuales (flatten, lock, etc.)
  - Eventos del sistema (auto-create, webhook_blocked)
  - Decisiones por señal (StrategyDecision)

actor: username de UI (o "system" para eventos automáticos)
Nunca raises — si falla el audit, loguear error y continuar
```

---

## Épica 9 — Seguridad

### REQ-0901 — Tokens de webhook

```text
- Generados desde UI ([Generar token] → mostrar UNA vez)
- Almacenados como SHA256+salt en DB
- Validación en cada request webhook
- Token inválido → 401 + AuditLog security_event
- Nunca loguear token en texto plano
```

### REQ-0902 — Seguridad UI

```text
- MVP puede funcionar sin login en red privada
- Código prepara middleware de autenticación
- Separar completamente lógica de trading de UI
- Logs sin tokens, passwords ni secrets
```

---

## Épica 10 — Tests

### REQ-1001 — Cobertura mínima

```text
Health y UI:
  GET /health → 200
  GET /ui, /ui/strategies, /ui/signals, /ui/positions,
  /ui/symbol-map, /ui/assets, /ui/settings, /ui/audit → 200

Webhook y normalización:
  Token inválido → 401
  Payload válido → 200, RawSignal creado
  ticker_received almacenado exactamente como llegó
  sentiment="flat" → action="exit"
  Símbolo desconocido → BLOCK symbol_not_mapped

Symbol Mapper:
  "MES" → "MESU2025", "MNQ" → "MNQU2025", "MJY" → "MJYU2025"
  "6J" → "6JU2025", "M6J" → None (BLOCK), "MES1!" → None (BLOCK)

Pipeline (todos los niveles):
  L1: system_paused → BLOCK (L2-L5 no evaluados)
  L1: strategy_candidate → QUEUE_FOR_REVIEW
  L1: NT inactivo + entry → BLOCK market_data_not_active
  L1: NT inactivo + exit → PERMIT con warning
  L2: fuera de horario MES → BLOCK
  L2: 6J a las 02:00 ET → PASS (sesión 24h)
  L2: salida fuera de horario + allow_exits=true → PASS
  L2: noticia activa → BLOCK
  L3: daily_loss_stop → BLOCK
  L4 MVP: score=100 siempre, siempre pasa
  L5: sl calculado correctamente LONG y SHORT
  L5: ATR no disponible → BLOCK
  Exit: salta L4, sin SL en payload

SLTPCalculator:
  MES SHORT 5500.00, ATR=8.0, mult=2.0 → SL=5516.00
  ATR=None → BLOCK atr_calculation_failed
  SL > 5% → BLOCK sl_too_wide

Config Resolver:
  Herencia global → asset → strategy
  None en asset no sobreescribe global

MarketDataService:
  Heartbeat reciente → is_active=True
  Heartbeat antiguo (>60s) → is_active=False
  Archivo no existe → is_active=False
  ATR calculado correctamente desde barras
  ATR=None con barras insuficientes

PayloadBuilder:
  Entrada → payload incluye stopLoss
  Salida → payload sin stopLoss
  Ticker en payload es el mapeado (MESU2025, no MES)

Estrategias (UI):
  Crear estrategia → visible en lista
  Cambiar status → AuditLog generado
  Clonar → nueva estrategia en candidate
```

---

## Definición de Done

```text
Una tarea se considera completa cuando:
1. Código implementado y revisado.
2. Tests pasan (pytest sin errores).
3. No hay secrets hardcodeados.
4. No hay yfinance directo fuera de YfinanceProvider.
5. No hay lecturas de /mnt/ntbridge fuera de NinjaTraderBridgeProvider.
6. Tests usan MockMarketDataProvider (nunca providers reales).
7. Migración Alembic si cambia el esquema de DB.
8. UI actualizada si la tarea afecta configuración operativa.
9. Decisiones importantes se auditan.
10. docker compose up en NTDEV levanta sin errores.
```
