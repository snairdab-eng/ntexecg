# NTEXECG — Prompts para Claude Code v1.0

---

## Cómo usar estos prompts

Ejecutar secuencialmente en Claude Code desde NTDEV (VS Code). Cada prompt produce código funcional y testeable antes de avanzar al siguiente.

**Reglas globales para Claude Code:**

```text
1. strategy_id SIEMPRE del URL path, NUNCA del payload.
2. ticker_received = exactamente payload["ticker"] sin modificar.
   Symbol Mapper: WHERE tv_symbol = ticker_received (búsqueda exacta).
   PROHIBIDO: lógica de strings, prefijos, transformaciones.
3. sentiment="flat" SIEMPRE produce action="exit".
4. Toda entrada APROBADA DEBE tener sl_price calculado.
   Si ATR no disponible → BLOCK (nunca aprobar sin SL).
5. Filtros: 5 niveles en orden estricto, fail-fast.
6. Tests SIEMPRE usan MockMarketDataProvider.
   Nunca yfinance real ni archivos del bridge en tests.
7. QualityScorer Fase 1: retorna score=100 siempre (stub).
8. HMMService Fase 1: retorna "unknown" siempre (stub).
9. Rutas con forward slash. Line endings LF.
10. La UI no contiene lógica de trading.
```

---

## PROMPT 1 — Estructura base del proyecto

```text
Create the initial NTEXECG project structure.

Tech stack:
- Python 3.12, FastAPI, SQLAlchemy 2.x async, Alembic
- Pydantic v2 with pydantic-settings
- Jinja2 templates, HTMX, Tailwind CSS (CDN), Alpine.js (CDN)
- loguru, APScheduler, httpx, pandas-ta, yfinance
- pytest, pytest-asyncio, aiosqlite (tests)

Create full directory structure per doc 03 v1.0.

app/core/config.py — Pydantic Settings with:
  APP_ENV, APP_NAME, APP_VERSION
  DATABASE_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
  SECRET_KEY, WEBHOOK_TOKEN_SALT, LUXALGO_WEBHOOK_SECRET
  TRADERSPOST_ENABLED (false), DRY_RUN (true)
  DEFAULT_TIMEZONE (America/New_York), LOG_LEVEL
  MAX_RETRY_ATTEMPTS (3), RETRY_BACKOFF_SECONDS (1)
  MARKET_DATA_PROVIDER (ninja_trader_bridge)
  NTBRIDGE_PATH (/mnt/ntbridge)
  NTBRIDGE_HEARTBEAT_MAX_AGE (60)
  MARKET_DATA_FALLBACK_ENABLED (false)
  NEWS_CACHE_TTL_MINUTES (60)

app/core/security.py:
  hash_token(token: str, salt: str) -> str (SHA256)
  verify_token(token, salt, hash) -> bool

app/main.py:
  create_app() factory with lifespan
  Include routers: health, webhooks_luxalgo, all web routes
  Mount static files
  Initialize MarketDataService in lifespan based on MARKET_DATA_PROVIDER
  Store in app.state.market_data

GET /health → {"status": "ok", "version": APP_VERSION, "env": APP_ENV}

GET /ui → dashboard.html (empty/zero metrics, DRY RUN badge if active)

templates/base.html:
  Tailwind CSS + HTMX + Alpine.js via CDN
  Navbar: Dashboard, Estrategias, Señales, Posiciones, Activos,
          Symbol Mapper, Templates, Settings, Audit
  Global mode badge (color per mode)
  DRY RUN badge (orange, visible only when DRY_RUN=true)
  Bridge status badge (HTMX polling 30s)
  Flash messages area, main content block, footer

docker-compose.yml (NTEXECG production):
  app: build, restart always, env_file, expose 8000
       volume /mnt/ntbridge:/mnt/ntbridge:ro
  db: postgres:16-alpine, no port exposure, healthcheck
  proxy: nginx:alpine, ports 80/443

docker-compose.dev.yml (NTDEV development):
  app: hot reload, port 8000:8000, mount .:/app
       environment MARKET_DATA_PROVIDER=yfinance
       NO /mnt/ntbridge mount
  db: postgres:16-alpine, port 5432:5432 exposed

.gitattributes: force LF for all text files

pyproject.toml: all dependencies per doc 03 v1.0

tests/conftest.py:
  SQLite in-memory database for all tests
  Override DATABASE_URL setting
  MockMarketDataProvider fixture:
    class MockMarketDataProvider(MarketDataProvider):
        async def get_bars(self, *a, **kw): return []
        async def get_atr(self, *a, **kw): return 8.0
        async def is_active(self, symbol): return True
  FastAPI TestClient fixture using MockMarketDataProvider

tests/test_health.py:
  GET /health → 200 with {"status": "ok"}
  GET /ui → 200

README.md: complete setup instructions for NTDEV
```

---

## PROMPT 2 — Modelos de base de datos y migraciones

```text
Implement all SQLAlchemy models and Alembic initial migration.

Create all model files per doc 04 v1.0:
  raw_signal.py, normalized_signal.py, strategy.py,
  global_profile.py, asset_profile.py, strategy_profile.py,
  symbol_map.py, decision.py, position_state.py,
  webhook_delivery.py, conflict_log.py, audit_log.py,
  strategy_performance.py, strategy_template.py,
  market_data_status.py, economic_event.py,
  ohlcv_bar.py (empty placeholder for Phase 5)

Key field in normalized_signal: "ticker_received" (not "symbol_raw")
Key field in position_state: "state_source" DEFAULT "estimated"

Create alembic initial migration:
  alembic revision --autogenerate -m "initial_schema"
  Verify migration includes all tables and indexes
  Implement reversible downgrade

Create scripts/seed_dev_data.py with:
  GlobalProfile (dry_run=true, traderspost_enabled=false)
  SymbolMaps per doc 04 (MES, MNQ, MYM, M2K, MGC, MJY, M6E, 6J, 6E)
    with pine_script_config and contract_type
    tv_symbol format: "MES" NOT "MES1!"
  AssetProfiles with session_config_json per doc 01:
    MES/MNQ: pit session (09:30-15:45 ET, Mon-Fri, next_day_end=false)
    MGC: 08:20-13:30 ET
    MJY/M6E/6J/6E: 24h session (18:00-17:00 ET, Sun-Fri, next_day_end=true)
  StrategyTemplate: "LuxAlgo Confirmation Normal"
  MarketDataStatus: one row per symbol, is_active=false initially
  GlobalSettings: market_data_provider, heartbeat_max_age, etc.

Create basic repository functions in app/services/:
  get_strategy_by_id, create_strategy
  get_active_symbol_map(db, tv_symbol: str) -> SymbolMap | None
    (exact match, no string manipulation)
  get_global_profile, get_asset_profile, get_strategy_profile
  get_position_state, upsert_position_state
  create_strategy_decision, create_audit_log
  upsert_market_data_status

tests/test_models.py:
  Create each model and verify it can be inserted/queried
  Test unique constraint on strategy.strategy_id
  Test unique constraint on normalized_signal.dedupe_key
  Test get_active_symbol_map("MES") → "MESU2025"
  Test get_active_symbol_map("M6J") → None (does not exist)
  Test get_active_symbol_map("MES1!") → None (wrong format)
```

---

## PROMPT 3 — Symbol Mapper y Signal Normalizer

```text
Implement SymbolMapper and SignalNormalizer.

CRITICAL DESIGN PRINCIPLE:
The operator manually configures the ticker in LuxAlgo.
NTEXECG never transforms, infers, or guesses the ticker.
Symbol Mapper does DIRECT LOOKUP only — no string manipulation.

app/services/symbol_mapper.py:

class SymbolMapper:
    async def map_symbol(self, db, ticker_received: str) -> str | None:
        # Direct DB lookup: WHERE tv_symbol = ticker_received AND active = true
        # Cache results 5 minutes
        # Return mapped_symbol or None if not found
        # NEVER manipulate the string (no prefix logic, no startswith)

    async def get_pine_script_config(self, db, tv_symbol: str) -> str | None:
        # Returns pine_script_config for UI display
        # e.g. '"ticker": "MJY"'

    async def check_expiring_contracts(self, db, alert_days=7):
        # Returns contracts expiring within N days

Tests:
  "MES"   → "MESU2025"  ✅
  "MNQ"   → "MNQU2025"  ✅
  "MJY"   → "MJYU2025"  ✅ (micro Yen — not "M6J")
  "M6E"   → "M6EU2025"  ✅
  "6J"    → "6JU2025"   ✅ (full size Yen)
  "6E"    → "6EU2025"   ✅
  "M6J"   → None        ✅ (does not exist in CME)
  "MES1!" → None        ✅ (wrong format)
  "XYZ"   → None        ✅

app/services/signal_normalizer.py:

class SignalNormalizer:
    async def normalize(self, db, raw_signal_id, strategy_id, payload) -> NormalizedSignal:

    Field: ticker_received = payload.get("ticker", "").strip()
           (stored exactly as received, never modified)

    Field: symbol = await symbol_mapper.map_symbol(db, ticker_received)
           (None if not found — pipeline Level 1.4 will BLOCK)

    CRITICAL: sentiment="flat" → action="exit" (ALWAYS)
    sentiment="long"/"short" → action stays as received from payload

    Timeframe normalization:
      "1"→"1m", "3"→"3m", "5"→"5m", "15"→"15m", "30"→"30m",
      "60"→"1h", "120"→"2h", "240"→"4h", "D"/"1D"→"1d", "W"→"1w"

    Dedupe key: SHA256(source:strategy_id:ticker_received:action:ts_minute)

Tests:
  payload ticker="MJY" → ticker_received="MJY", symbol="MJYU2025"
  payload ticker="M6J" → ticker_received="M6J", symbol=None
  payload ticker="6J"  → ticker_received="6J",  symbol="6JU2025"
  sentiment="flat"     → action="exit" regardless of action field
  sentiment="short"    → action stays as received
  interval="5"         → timeframe="5m"
  interval="D"         → timeframe="1d"
  ticker_received stored EXACTLY as received, never modified
```

---

## PROMPT 4 — Webhook Receiver LuxAlgo

```text
Implement LuxAlgo webhook endpoint.

POST /webhooks/luxalgo/{strategy_id}?token={secret}

1. Extract strategy_id from path (NOT from payload)
2. Extract token from query param
3. Always create RawSignal with token_valid=false initially
4. Validate token: SHA256(token + WEBHOOK_TOKEN_SALT) must match
   strategy.webhook_token OR match LUXALGO_WEBHOOK_SECRET (for unknown strategies)
5. If invalid: update raw_signal.token_valid=false, AuditLog(WEBHOOK_BLOCKED), 401
6. If valid: update raw_signal.token_valid=true, 200 immediately:
   {"received": true, "signal_id": raw_signal.id}
7. Launch background task: process_luxalgo_signal(raw_signal.id, strategy_id)

Background task process_luxalgo_signal:
  1. SignalNormalizer.normalize()
  2. Deduplicator.is_duplicate() → IGNORE_DUPLICATE
  3. StrategyRegistry.get_or_create() → QUEUE_FOR_REVIEW if candidate
  4. ConfigResolver.resolve()
  5. FilterPipeline.evaluate() (stub OK for this prompt)
  6. Create StrategyDecision
  7. If APPROVE: dispatch to TradersPost (stub OK)
  8. PositionService.update()
  9. PerformanceTracker.update()

Add StrategyRegistry.get_or_create_strategy():
  If exists: return (strategy, False)
  If not exists:
    Create Strategy(status=candidate, enabled=false)
    Create StrategyProfile with GlobalProfile defaults
    AuditLog(actor="system", action="CREATE", object_type="Strategy")
    Return (strategy, True)

Tests:
  Valid token + valid payload → 200, RawSignal created, token_valid=True
  Invalid token → 401, RawSignal created with token_valid=False
  Unknown strategy_id → strategy auto-created as candidate
  sentiment=flat → NormalizedSignal.action="exit"
  ticker="MJY" → ticker_received="MJY", symbol="MJYU2025"
  ticker="M6J" → ticker_received="M6J", symbol=None, BLOCK in pipeline
  Duplicate within 60s → IGNORE_DUPLICATE decision
  Background task error → error logged, no unhandled exception
```

---

## PROMPT 5 — MarketDataService y providers

```text
Implement MarketDataService abstraction with all providers.

app/services/market_data_service.py:

class MarketDataProvider(ABC):
    @abstractmethod
    async def get_bars(self, symbol, timeframe, count=300) -> list[dict] | None
    @abstractmethod
    async def get_atr(self, symbol, timeframe, period=14) -> float | None
    @abstractmethod
    async def is_active(self, symbol) -> bool

    def _bars_to_dataframe(self, bars) -> pd.DataFrame:
        # Rename t→time, o→open, h→high, l→low, c→close, v→volume

    def _calculate_atr(self, bars, period) -> float | None:
        # Use pandas-ta ATR calculation
        # Return None if insufficient bars (< period + 1)

class NinjaTraderBridgeProvider(MarketDataProvider):
    # Reads /mnt/ntbridge/bars_{symbol}_{tf}.json
    # is_active(): checks mtime of heartbeat_{symbol}.json < heartbeat_max_age
    # NOT the content of the file, just the modification time
    # get_atr(): read bars JSON, calculate with _calculate_atr()

class YfinanceProvider(MarketDataProvider):
    TICKER_MAP = {
        "MES":"MES=F", "MNQ":"MNQ=F", "MYM":"MYM=F", "M2K":"M2K=F",
        "MGC":"MGC=F", "MJY":"MJY=F", "M6E":"M6E=F", "6J":"6J=F", "6E":"6E=F"
    }
    # is_active(): always True in dev

class TradovateAPIProvider(MarketDataProvider):
    # All methods: raise NotImplementedError (Phase 5+ stub)

class DatabentoProvider(MarketDataProvider):
    # All methods: raise NotImplementedError (Phase 5+ stub)

class MarketDataService:
    def __init__(self, provider: MarketDataProvider): ...
    async def get_atr(self, symbol, timeframe, period=14) -> float | None
    async def is_active(self, symbol) -> bool
    def get_provider_name(self) -> str

Provider factory in app/main.py lifespan:
    Create provider based on settings.MARKET_DATA_PROVIDER
    Store in app.state.market_data

HeartbeatMonitor in app/core/scheduler.py:
    APScheduler task every 30 seconds
    For each active symbol in symbol_maps:
      Check is_active(), get ATR if active
      Update market_data_status table
      Create dashboard alert if became inactive

tests/fixtures/bridge/:
    bars_MES_5m.json: 20+ OHLCV bars for testing
    heartbeat_MES.json: valid heartbeat file

tests/test_market_data_service.py:
    Use tmp_path fixture for file-based tests
    Heartbeat recent → is_active=True
    Heartbeat old (mtime > 60s ago) → is_active=False
    Heartbeat missing → is_active=False
    get_atr with 20 bars → returns float > 0
    get_atr with 5 bars (insufficient) → returns None
    MarketDataService delegates to provider
    MockMarketDataProvider for pipeline tests:
      always returns atr=8.0, is_active=True
```

---

## PROMPT 6 — Filter Pipeline completo (5 niveles)

```text
Implement FilterPipeline with all 5 levels.

app/services/filter_pipeline.py:

@dataclass
class PipelineResult:
    decision: str
    reason: str
    reason_detail: str
    score: int | None
    sl_price: float | None    # Only for APPROVE entries
    tp_price: float | None
    atr_value: float | None
    pipeline_execution: dict  # Detailed result per level

class FilterPipeline:
    def __init__(self, session_validator, news_filter,
                 quality_scorer, sl_tp_calculator,
                 position_service, market_data):

    async def evaluate(self, db, signal, strategy, config) -> PipelineResult:
        # Execute 5 levels in strict order
        # If any level fails: return immediately (fail-fast)
        # Exits skip Level 4 and skip Level 5 SL calculation

LEVEL 1 — System validation (6 checks):
    1.1 global_mode: PAUSED/FLATTEN_ONLY → BLOCK (exits always pass)
    1.2 strategy_status: candidate→QUEUE, retired/quarantined→BLOCK,
                          paused+entry→BLOCK, paused+exit→continue
    1.3 duplicate: already checked but defensive check
    1.4 symbol_map: signal.symbol is None → BLOCK symbol_not_mapped
                    Include ticker_received in error detail for operator
    1.5 allowed_symbols: ticker_raw not in config → BLOCK symbol_not_allowed
    1.6 market_data active: await market_data.is_active(signal.ticker_received)
                             Entry: BLOCK market_data_not_active
                             Exit: PERMIT with warning

LEVEL 2 — Temporal (SessionValidator + NewsFilter):
    2.1 Day of week per session_config_json of asset
    2.2 Session window per session_config_json (handle next_day_end=true)
    2.3 News window check
    Exits: if outside window + allow_exits_outside_window=true → PERMIT

LEVEL 3 — Risk:
    3.1 daily_loss_stop (global and strategy)
    3.2 max_positions (symbol and global)
    3.3 position_state (LOCKED, UNKNOWN+entry, FLAT+exit)

LEVEL 4 — Score (entries only):
    Call QualityScorer.calculate() — returns 100 in Phase 1 (stub)
    If score < score_minimum → BLOCK score_below_threshold
    Exits: skip this level entirely

LEVEL 5 — SL/TP (entries only):
    Call SLTPCalculator.calculate()
    If not passed: BLOCK with reason from calculator
    sl_price is MANDATORY for approved entries
    Exits: skip this level entirely

app/services/session_validator.py:
    Validate day of week and session window from session_config_json
    Handle 24h sessions (next_day_end=true for MJY, 6J, 6E):
      valid = signal_time >= entry_start OR signal_time <= entry_end
    Handle pit sessions (next_day_end=false for MES, MNQ):
      valid = entry_start <= signal_time <= entry_end

app/services/news_filter.py:
    Check economic_events table for events in ±news_window_minutes
    Filter by news_impact_levels_json
    Update cache from ForexFactory if TTL expired

app/services/quality_scorer.py:
    Phase 1 stub: return ScoreResult(score=100, breakdown={},
                                     note="MVP placeholder - Phase 5")

app/services/sl_tp_calculator.py:
    Uses market_data.get_atr(symbol, timeframe, period)
    LONG:  sl = entry - (atr × sl_mult)
    SHORT: sl = entry + (atr × sl_mult)
    Validate: |sl - entry| / entry <= max_sl_pct (default 5%)
    TP: None if tp_atr_multiplier not set
    INVARIANT: Never return passed=True with sl_price=None

tests/test_filter_pipeline.py — comprehensive suite:
    L1: system_paused → BLOCK (L2-L5 not evaluated)
    L1: strategy_candidate → QUEUE_FOR_REVIEW
    L1: symbol=None → BLOCK symbol_not_mapped (with ticker_received in detail)
    L1: NT inactive + entry → BLOCK market_data_not_active
    L1: NT inactive + exit → PERMIT with warning
    L2: MES outside 09:30-15:45 ET → BLOCK
    L2: MJY at 02:00 ET → PASS (24h session)
    L2: exit outside window + allow_exits=true → PASS
    L2: news active → BLOCK
    L3: daily_loss_stop → BLOCK
    L4 (stub): score=100 always passes
    L5: sl calculated correctly for LONG and SHORT
    L5: ATR=None → BLOCK atr_calculation_failed
    Exit full path: APPROVE without sl_price in result
    Entry full path: APPROVE with sl_price, atr_value in result
    pipeline_execution dict contains result for each level

tests/test_session_validator.py:
    MES at 10:30 ET Tuesday → passed
    MES at 16:02 ET Tuesday → failed
    MES exit at 16:02 + allow_exits=true → passed
    MES on Saturday → failed
    MJY at 02:00 ET Tuesday → passed (24h)
    6J at 17:30 ET Friday → failed (after 17:00 close)

tests/test_sl_tp_calculator.py:
    MES SHORT 5500.00, ATR=8.0, mult=2.0 → SL=5516.00
    MNQ LONG 21500.0, ATR=30.0, mult=2.0 → SL=21440.0
    ATR=None → BLOCK atr_calculation_failed
    SL distance > 5% → BLOCK sl_too_wide
    With tp_mult → tp_price calculated
    Without tp_mult → tp_price=None
```

---

## PROMPT 7 — TradersPost Dispatcher

```text
Implement PayloadBuilder and TradersPostClient.

app/services/payload_builder.py:

def build(signal, strategy, config, decision, pipeline_result) -> dict:
    payload = {
        "ticker":      signal.symbol,        # MESU2025 (mapped)
        "action":      signal.action,
        "sentiment":   derive_sentiment(signal.action),
        "signalPrice": signal.price,
        "quantity":    resolve_quantity(strategy.mode, config, signal),
    }

    # For entry signals: ALWAYS include stopLoss
    if signal.signal_role in ["entry_long", "entry_short",
                               "reversal_to_long", "reversal_to_short"]:
        if pipeline_result.sl_price is None:
            raise ValueError(f"Entry signal without sl_price is forbidden. "
                             f"signal_id={signal.id}")
        payload["stopLoss"] = {"type": "stop", "price": pipeline_result.sl_price}
        if pipeline_result.tp_price is not None:
            payload["takeProfit"] = {"type": "limit", "price": pipeline_result.tp_price}

    # For exit signals: NO stopLoss (position is being closed)

    payload["extras"] = {
        "strategy_id":    signal.strategy_id,
        "signal_id":      str(signal.id),
        "ntexecg_score":  pipeline_result.score,
        "atr_value":      pipeline_result.atr_value,
        "sl_multiplier":  config.get("sl_atr_multiplier"),
        "provider":       pipeline_result.market_data_provider,
    }
    return payload

app/services/traderspost_client.py:

class TradersPostClient:
    async def send(self, webhook_url, payload, decision_id, dry_run) -> WebhookDelivery:
        if dry_run:
            return WebhookDelivery(status="DRY_RUN", payload_json=payload, ...)

        # Mask URL token for storage/logging
        url_masked = mask_token_in_url(webhook_url)

        # POST with httpx, timeout=10s
        # Retry: max 3 attempts, backoff 1s/2s/4s
        # Exit signals: up to 10 retries (critical)
        # Entry signals: do NOT retry if > entry_signal_timeout_secs since signal_ts

        return WebhookDelivery(status="SENT"/"FAILED", ...)

Wire up in background task after APPROVE:
    payload = payload_builder.build(signal, strategy, config, decision, pipeline_result)
    webhook_url = strategy_profile.traderspost_webhook_url or global fallback
    dry_run = config.get("dry_run", True)
    delivery = await traderspost_client.send(webhook_url, payload, decision.id, dry_run)

Tests (using httpx MockTransport):
    dry_run=True → WebhookDelivery(status=DRY_RUN), no HTTP call
    dry_run=False + 200 → WebhookDelivery(status=SENT)
    dry_run=False + 500 → retry, eventually FAILED
    Entry payload → contains stopLoss key
    Exit payload → does NOT contain stopLoss key
    Entry without sl_price → raises ValueError
    Ticker in payload is mapped symbol (MESU2025, not MES)
    URL with token is masked in WebhookDelivery.url_masked
```

---

## PROMPT 8 — Interfaz web completa

```text
Implement the complete MVP web interface.

Follow doc 02 v1.0 for all pages, layout, and UX details.

Key implementations:

1. GET /api/assets/{symbol}/pine-script-config endpoint:
   Returns {"pine_script_config": '"ticker": "MJY"'}
   Used by strategy form Alpine.js on asset selection

2. Strategy form with ticker hint:
   When asset is selected, fetch pine_script_config and show:
   "Ticker para LuxAlgo: "ticker": "MJY"  [Copiar]"

3. Signal detail with full pipeline breakdown:
   Show each level: ✅/❌/⏭ with detail
   For APPROVE: show SL price, ATR value, provider
   For BLOCK: highlight failing level and reason with detail

4. Dashboard bridge status (HTMX partial, poll 30s):
   GET /ui/bridge-status → partials/bridge_status.html
   Show per-symbol: active/inactive, ATR 5m, heartbeat age
   Critical alert if any symbol inactive

5. Confirmation modals (Alpine.js):
   For "live" status change: require user to type "CONFIRMAR"
   For quarantine/retire: require text reason input
   For other dangerous actions: simple confirm button

6. POST /ui/strategies/{id}/clone:
   Copy strategy + profile, ask for new strategy_id and symbol
   New strategy starts in candidate status
   AuditLog(action="CLONE", old=source_id, new=new_id)

7. Batch actions POST /ui/strategies/batch-action:
   Accepts strategy_ids[], action
   AuditLog per affected strategy

8. Position State warning (prominent):
   "⚠️ Estado estimado — verificar en TradersPost/NinjaTrader"
   Show on all position-related views

Tests:
    All pages return 200
    POST /ui/strategies/new creates strategy + profile + AuditLog
    Status change → AuditLog with old and new values
    Clone creates new strategy in candidate
    Batch pause → AuditLog per strategy
```

---

## PROMPT 9 — Position Service, Audit Service, y PerformanceTracker

```text
Implement PositionService, AuditService, and PerformanceTracker.

app/services/position_service.py:
    get_state(db, account_id, symbol) → PositionState (create FLAT if not exists)
    on_entry_approved(db, account_id, symbol, direction, qty, price, strategy_id, signal_id)
        → set PENDING_LONG / PENDING_SHORT, record entry data
    on_delivery_confirmed(db, account_id, symbol)
        → PENDING_LONG→LONG, PENDING_SHORT→SHORT, EXITING→FLAT
    on_exit_approved(db, account_id, symbol) → set EXITING
    on_flatten_manual(db, account_id, symbol, actor) → EXITING + AuditLog(FLATTEN)
    on_lock(db, account_id, symbol, actor) → LOCKED + AuditLog(LOCK)
    on_unlock(db, account_id, symbol, actor) → restore state + AuditLog(UNLOCK)
    All states have state_source="estimated" in MVP

app/services/audit_service.py:
    log(db, actor, action, object_type, object_id, old_value, new_value, reason, ip)
    log_strategy_change(db, actor, strategy, old_data, new_data, action, reason)
    log_system_event(db, action, object_type, object_id, details)
    RULE: Never raise exceptions from AuditService. Log error and continue.
    actor="system" for automated events, actor="admin" for UI in MVP

app/services/performance_tracker.py:
    update(db, strategy_id, decision) → update StrategyPerformance counters
    Increment total_signals_received always
    Increment blocks_level_N based on pipeline_execution_json
    Update top_block_reasons_json (sorted by frequency)
    Update filter_pass_rate, avg_score

Tests:
    PositionState: FLAT+buy → PENDING_LONG → LONG → EXITING → FLAT
    PositionState: flatten_manual → AuditLog(FLATTEN)
    PositionState: lock/unlock → correct state + AuditLog
    AuditService: never raises even if DB fails
    AuditService: strategy change logged with old/new values
    PerformanceTracker: counters increment correctly
    PerformanceTracker: top_block_reasons updated correctly
```

---

## PROMPT 10 — Scripts de utilería y documentación final

```text
Create utility scripts and finalize documentation.

scripts/simulate_webhook.py:
    Simulate LuxAlgo webhook from NTDEV terminal
    Usage:
      python scripts/simulate_webhook.py \
        --strategy-id mes5m_confirmation_normal \
        --action sell --ticker MES --sentiment short \
        --price 5500.00 --interval 5 --token tu-token
    Options: --exit (flat), --dry (show payload without sending)

scripts/rollover_alert.py:
    Check contracts expiring within N days
    Usage: python scripts/rollover_alert.py --days 7

scripts/mount_ntbridge.sh:
    Mount \\NTRADER\bridge on NTEXECG (Ubuntu)
    See doc 07 v1.0 for full script content

scripts/backup_db.py:
    Manual DB backup
    Creates compressed SQL dump

Update README.md with:
  Prerequisites per environment (NTDEV, NTEXECG, NTRADER)
  Setup instructions for NTDEV (Docker Desktop, Claude Code, etc.)
  Setup instructions for NTEXECG (Docker Engine, Samba mount, etc.)
  NinjaTrader bridge setup in NTRADER
  How to run tests: docker compose -f docker-compose.dev.yml exec app pytest -v
  How to simulate webhook: python scripts/simulate_webhook.py ...
  How to configure LuxAlgo webhook URL (the format)
  LuxAlgo JSON payload format with manual ticker
  How to use dry_run mode
  How to deploy to NTEXECG via VPN

Final verification (PROMPT 10 checklist):
  - No hardcoded secrets anywhere
  - ticker_received stored exactly as received (test proves it)
  - symbol=None for unknown tickers → BLOCK (test proves it)
  - Entry without sl_price → ValueError (code prevents it)
  - Tests use only MockMarketDataProvider (grep confirms it)
  - All 5 pipeline levels tested with fail-fast behavior
  - DRY_RUN=true by default in production .env.example
  - TRADERSPOST_ENABLED=false by default
  - .env in .gitignore
  - LF line endings enforced via .gitattributes
  - docker compose -f docker-compose.dev.yml up -d works on NTDEV
  - All pages in /ui return 200
  - pytest -v --cov=app passes with coverage > 70%
```
