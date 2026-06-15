# NTEXECG — Modelo de Datos v1.0

---

## Principios del modelo

```text
1. Toda señal se guarda desde raw hasta decisión final.
2. Configuración operativa en DB, nunca en código.
3. Herencia global → asset → strategy en 3 tablas separadas.
4. Nada se borra. Todo se desactiva o archiva.
5. Cada cambio de configuración genera AuditLog.
6. Timestamps siempre en UTC.
7. ticker_received = exactamente lo que llegó del payload.
```

---

## 1. raw_signals

Señal original exactamente como llegó. Inmutable después de inserción.

```sql
id              UUID        PRIMARY KEY DEFAULT gen_random_uuid()
source          VARCHAR(50) NOT NULL    -- "luxalgo", "tradingview"
strategy_id     VARCHAR(100) NOT NULL   -- extraído del URL path
payload_json    JSONB       NOT NULL    -- payload original sin modificar
headers_json    JSONB
ip_address      VARCHAR(45)
token_valid     BOOLEAN     NOT NULL DEFAULT false
received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

INDEX ON raw_signals(strategy_id)
INDEX ON raw_signals(received_at DESC)
```

---

## 2. normalized_signals

```sql
id              UUID        PRIMARY KEY DEFAULT gen_random_uuid()
raw_signal_id   UUID        NOT NULL REFERENCES raw_signals(id)
source          VARCHAR(50) NOT NULL
strategy_id     VARCHAR(100) NOT NULL
ticker_received VARCHAR(50) NOT NULL
-- Exactamente lo que llegó en payload["ticker"]
-- Configurado manualmente por el operador en LuxAlgo
-- Ej: "MJY", "MES", "6J"

symbol          VARCHAR(50)
-- Contrato vigente después del Symbol Mapper
-- Ej: "MJYU2025", "MESU2025"
-- NULL si Symbol Mapper no encontró mapeo (→ BLOCK)

timeframe       VARCHAR(20)             -- "5m", "1h", "4h"
action          VARCHAR(20) NOT NULL    -- "buy", "sell", "exit", "cancel", "unknown"
price           NUMERIC(18,6)
quantity        INTEGER
signal_ts       TIMESTAMPTZ NOT NULL
signal_role     VARCHAR(30)
-- entry_long, entry_short, exit_long, exit_short,
-- reversal_to_long, reversal_to_short, cancel, unknown

dedupe_key      VARCHAR(64) NOT NULL
status          VARCHAR(30) NOT NULL DEFAULT 'pending'
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE INDEX ON normalized_signals(dedupe_key)
INDEX ON normalized_signals(strategy_id)
INDEX ON normalized_signals(signal_ts DESC)
```

---

## 3. strategies

```sql
id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid()
strategy_id             VARCHAR(100) NOT NULL UNIQUE
name                    VARCHAR(200) NOT NULL
source                  VARCHAR(50) NOT NULL    -- "luxalgo", "tradingview"
symbol                  VARCHAR(50)             -- ticker base: "MES", "MJY"
timeframe               VARCHAR(20)
strategy_type           VARCHAR(50) NOT NULL DEFAULT 'unknown'
-- trend_following, momentum_continuation, mean_reversion,
-- breakout, scalping, hybrid, unknown

status                  VARCHAR(30) NOT NULL DEFAULT 'candidate'
-- candidate, shadow, paper, micro, limited_live, live,
-- paused, quarantined, retired

enabled                 BOOLEAN     NOT NULL DEFAULT false
webhook_token           VARCHAR(128)            -- hash SHA256+salt
traderspost_webhook_url TEXT
template_id             UUID        REFERENCES strategy_templates(id)
pine_script_ticker_note TEXT
-- Instrucción para UI: '"ticker": "MJY"'

luxalgo_metrics_json    JSONB
-- {"win_rate": 83.81, "profit_factor": 3.99,
--  "max_drawdown_pct": 14.32, "max_drawdown_usd": 8900,
--  "net_profit_usd": 52337.50, "total_trades": 105,
--  "evaluation_start": "2026-02-13"}

notes                   TEXT
created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
retired_at              TIMESTAMPTZ
retired_reason          TEXT

UNIQUE INDEX ON strategies(strategy_id)
INDEX ON strategies(status)
```

---

## 4. global_profile

Config base del sistema. Un solo registro activo.

```sql
id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid()
profile_name                VARCHAR(50) NOT NULL DEFAULT 'default'

-- Horario global por defecto
timezone                    VARCHAR(50) NOT NULL DEFAULT 'America/New_York'
days_enabled_json           JSONB       NOT NULL DEFAULT '[1,2,3,4,5]'
entry_start_time            TIME        NOT NULL DEFAULT '09:30'
entry_end_time              TIME        NOT NULL DEFAULT '15:45'
allow_exits_outside_window  BOOLEAN     NOT NULL DEFAULT true
allow_overnight             BOOLEAN     NOT NULL DEFAULT false
force_flat_time             TIME                 DEFAULT '15:55'

-- Noticias
news_filter_enabled         BOOLEAN     NOT NULL DEFAULT true
news_window_minutes         INTEGER     NOT NULL DEFAULT 30
news_impact_levels_json     JSONB       NOT NULL DEFAULT '["high"]'

-- Riesgo global
max_open_positions          INTEGER     NOT NULL DEFAULT 5
global_daily_loss_stop      NUMERIC(12,2)
global_daily_profit_lock    NUMERIC(12,2)
entry_cutoff_time           TIME
max_holding_minutes         INTEGER

-- Score
default_score_minimum       INTEGER     NOT NULL DEFAULT 65

-- Sistema
global_mode                 VARCHAR(30) NOT NULL DEFAULT 'normal'
traderspost_enabled         BOOLEAN     NOT NULL DEFAULT false
dry_run                     BOOLEAN     NOT NULL DEFAULT true
default_quantity            INTEGER     NOT NULL DEFAULT 1

-- TradersPost retry
retry_attempts              INTEGER     NOT NULL DEFAULT 3
retry_backoff_seconds       INTEGER     NOT NULL DEFAULT 1
entry_signal_timeout_secs   INTEGER     NOT NULL DEFAULT 30

active                      BOOLEAN     NOT NULL DEFAULT true
version                     INTEGER     NOT NULL DEFAULT 1
updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_by                  VARCHAR(100)
```

---

## 5. asset_profiles

Config por activo. Sobreescribe global_profile donde se defina.

```sql
id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid()
symbol                      VARCHAR(50) NOT NULL UNIQUE
-- ticker exacto en LuxAlgo: "MES", "MNQ", "MJY", "6J"

description                 VARCHAR(200)
-- "Micro JPY/USD Futures — CME"

pine_script_config          VARCHAR(100)
-- '"ticker": "MJY"' — mostrado en UI al crear estrategia

contract_type               VARCHAR(30)
-- "futures_micro", "futures_large", "stocks"

session_config_json         JSONB
-- Ejemplo MES (pit session):
-- {"timezone": "America/New_York", "days_enabled": [1,2,3,4,5],
--  "entry_start": "09:30", "entry_end": "15:45",
--  "next_day_end": false, "avoid_open_minutes": 30,
--  "avoid_close_minutes": 15, "force_flat_time": "15:55",
--  "allow_overnight": false, "allow_exits_outside_window": true}
--
-- Ejemplo MJY (forex futures, casi 24h):
-- {"timezone": "America/New_York", "days_enabled": [0,1,2,3,4,5],
--  "entry_start": "18:00", "entry_end": "17:00",
--  "next_day_end": true, "avoid_open_minutes": 30,
--  "allow_overnight": true, "allow_exits_outside_window": true}

sl_atr_multiplier           NUMERIC(5,2) DEFAULT 2.0
tp_atr_multiplier           NUMERIC(5,2)            -- NULL = usar Builtin-Exits
atr_period                  INTEGER      DEFAULT 14
atr_timeframe               VARCHAR(10)             -- NULL = usar TF de la señal

max_trades_day              INTEGER
daily_loss_stop             NUMERIC(12,2)
max_quantity                INTEGER
max_open_positions_symbol   INTEGER     NOT NULL DEFAULT 1
score_minimum               INTEGER
allow_reversal              BOOLEAN     NOT NULL DEFAULT false
cooldown_minutes            INTEGER

active                      BOOLEAN     NOT NULL DEFAULT true
version                     INTEGER     NOT NULL DEFAULT 1
created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_by                  VARCHAR(100)
```

---

## 6. strategy_profiles

Config por estrategia. Sobreescribe asset_profile y global_profile.

```sql
id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid()
strategy_id                 VARCHAR(100) NOT NULL UNIQUE
                            REFERENCES strategies(strategy_id)
profile_name                VARCHAR(100)

-- TradersPost
traderspost_webhook_url     TEXT
routing_mode                VARCHAR(30) DEFAULT 'specific_accounts'
allowed_accounts_json       JSONB
allowed_symbols_json        JSONB
mode                        VARCHAR(20) DEFAULT 'paper'
-- "paper", "micro", "limited_live", "live"

-- Pipeline de filtros
pipeline_config_json        JSONB
-- {"score_minimum": 65, "filters": {
--   "volume_relative": {"enabled": false, "weight": 30, "threshold": 1.2},
--   "atr_normalized":  {"enabled": false, "weight": 25, "std_dev_max": 2.0},
--   "vwap_position":   {"enabled": false, "weight": 25},
--   "time_of_day":     {"enabled": false, "weight": 20},
--   "hmm_regime":      {"enabled": false, "allowed_regimes": ["trending_bull"]}
-- }}

-- SL/TP (sobreescribe asset_profile)
sl_atr_multiplier           NUMERIC(5,2)
tp_atr_multiplier           NUMERIC(5,2)
atr_period                  INTEGER
atr_timeframe               VARCHAR(10)

-- Herencia de horario (solo definir lo que difiere)
timezone                    VARCHAR(50)
days_enabled_json           JSONB
entry_start_time            TIME
entry_end_time              TIME
allow_exits_outside_window  BOOLEAN
allow_overnight             BOOLEAN
force_flat_time             TIME
max_holding_minutes         INTEGER
cooldown_minutes            INTEGER

-- Riesgo
max_trades_day              INTEGER
daily_loss_stop             NUMERIC(12,2)
daily_profit_lock           NUMERIC(12,2)
max_quantity                INTEGER
max_open_positions_symbol   INTEGER
allow_reversal              BOOLEAN

active                      BOOLEAN     NOT NULL DEFAULT true
version                     INTEGER     NOT NULL DEFAULT 1
created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_by                  VARCHAR(100)
```

---

## 7. symbol_maps

Búsqueda directa: `tv_symbol` → `mapped_symbol`. Sin lógica de prefijos.

```sql
id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid()
tv_symbol           VARCHAR(50) NOT NULL UNIQUE
-- Exactamente lo que llega en payload["ticker"]
-- "MES", "MNQ", "MJY", "6J" — NO "MES1!" ni "M6J"

mapped_symbol       VARCHAR(50) NOT NULL
-- Contrato vigente: "MESU2025", "MJYU2025", "6JU2025"

exchange            VARCHAR(20) NOT NULL    -- "CME", "CBOT", "COMEX"
contract_type       VARCHAR(30) NOT NULL    -- "futures_micro", "futures_large"
underlying_name     VARCHAR(100)
-- "Micro JPY/USD Futures", "E-mini S&P 500"

pine_script_config  VARCHAR(100) NOT NULL
-- '"ticker": "MJY"' — instrucción exacta para el JSON de LuxAlgo
-- Esta columna se muestra en UI del Symbol Mapper

expiry_date         DATE
active              BOOLEAN     NOT NULL DEFAULT true
notes               TEXT
created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
updated_by          VARCHAR(100)

INDEX ON symbol_maps(tv_symbol) WHERE active = true
```

Datos iniciales:
```text
tv_symbol  mapped_symbol  exchange  contract_type    expiry      pine_script_config
MES        MESU2025       CME       futures_micro    2025-09-19  "ticker": "MES"
MNQ        MNQU2025       CME       futures_micro    2025-09-19  "ticker": "MNQ"
MYM        MYMU2025       CBOT      futures_micro    2025-09-19  "ticker": "MYM"
M2K        M2KU2025       CME       futures_micro    2025-09-19  "ticker": "M2K"
MGC        MGCQ2025       COMEX     futures_micro    2025-08-27  "ticker": "MGC"
MJY        MJYU2025       CME       futures_micro    2025-09-15  "ticker": "MJY"
M6E        M6EU2025       CME       futures_micro    2025-09-15  "ticker": "M6E"
6J         6JU2025        CME       futures_large    2025-09-15  "ticker": "6J"
6E         6EU2025        CME       futures_large    2025-09-15  "ticker": "6E"
```

---

## 8. strategy_decisions

```sql
id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid()
normalized_signal_id    UUID        NOT NULL REFERENCES normalized_signals(id)
strategy_id             VARCHAR(100) NOT NULL
decision                VARCHAR(30) NOT NULL
-- APPROVE, BLOCK, PAPER_ONLY, MICRO_ONLY, REDUCE_SIZE,
-- IGNORE_DUPLICATE, EXIT_ONLY, FLATTEN_ONLY, QUEUE_FOR_REVIEW, ERROR

reason                  VARCHAR(100) NOT NULL
reason_detail           TEXT
score                   INTEGER
score_breakdown_json    JSONB

-- Desglose completo de cada nivel del pipeline
pipeline_execution_json JSONB
-- {"level_1": {"passed": true, "checks": {...}},
--  "level_2": {"passed": false, "failed_at": "2.2",
--              "reason": "outside_trading_window",
--              "current_time": "16:02 ET", "window": "09:30-15:45"},
--  "level_3": {"skipped": true},
--  ...}

-- SL/TP calculado (si se aprobó)
sl_price                NUMERIC(18,6)
tp_price                NUMERIC(18,6)
atr_value               NUMERIC(18,6)
sl_multiplier_used      NUMERIC(5,2)
market_data_provider    VARCHAR(50)     -- "NinjaTraderBridgeProvider"

config_snapshot_json    JSONB           -- config efectiva usada
created_at              TIMESTAMPTZ NOT NULL DEFAULT now()

INDEX ON strategy_decisions(normalized_signal_id)
INDEX ON strategy_decisions(strategy_id)
INDEX ON strategy_decisions(decision)
INDEX ON strategy_decisions(created_at DESC)
```

---

## 9. position_states

```sql
id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid()
account_id          VARCHAR(100) NOT NULL
symbol              VARCHAR(50) NOT NULL    -- contrato vigente: "MJYU2025"
position_state      VARCHAR(30) NOT NULL DEFAULT 'FLAT'
-- FLAT, LONG, SHORT, PENDING_LONG, PENDING_SHORT,
-- EXITING, REVERSING, LOCKED, UNKNOWN

direction           VARCHAR(10)
quantity            INTEGER     NOT NULL DEFAULT 0
entry_price         NUMERIC(18,6)
entry_time          TIMESTAMPTZ
source_strategy_id  VARCHAR(100)
active_signal_id    UUID
risk_plan_json      JSONB       -- SL/TP calculados al momento de entrada
state_source        VARCHAR(20) NOT NULL DEFAULT 'estimated'
-- "estimated" = basado en señales enviadas (MVP)
-- "confirmed" = confirmado por broker API (futuro)

updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()

UNIQUE INDEX ON position_states(account_id, symbol)
```

---

## 10. market_data_status

Estado del proveedor de datos por símbolo. Actualizada por APScheduler cada 30s.

```sql
id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid()
symbol                VARCHAR(50) NOT NULL UNIQUE    -- "MES", "MJY"
provider              VARCHAR(50) NOT NULL           -- "NinjaTraderBridgeProvider"
is_active             BOOLEAN     NOT NULL DEFAULT false
last_heartbeat_at     TIMESTAMPTZ
heartbeat_age_seconds INTEGER
last_atr_5m           NUMERIC(18,6)
last_atr_1h           NUMERIC(18,6)
bars_available_json   JSONB       -- {"5m": 300, "15m": 200, "1h": 250}
error_message         TEXT
updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
```

---

## 11. strategy_performance

Métricas reales acumuladas. Actualizada después de cada StrategyDecision.

```sql
id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid()
strategy_id             VARCHAR(100) NOT NULL UNIQUE
total_signals_received  INTEGER     NOT NULL DEFAULT 0
total_signals_approved  INTEGER     NOT NULL DEFAULT 0
total_signals_blocked   INTEGER     NOT NULL DEFAULT 0
total_signals_sent      INTEGER     NOT NULL DEFAULT 0
filter_pass_rate        NUMERIC(5,2)
avg_score               NUMERIC(5,2)
blocks_level_1          INTEGER     NOT NULL DEFAULT 0
blocks_level_2          INTEGER     NOT NULL DEFAULT 0
blocks_level_3          INTEGER     NOT NULL DEFAULT 0
blocks_level_4          INTEGER     NOT NULL DEFAULT 0
top_block_reasons_json  JSONB
-- {"outside_trading_window": 45, "score_below_threshold": 23}

first_signal_at         TIMESTAMPTZ
last_signal_at          TIMESTAMPTZ
updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
```

---

## 12. strategy_templates

```sql
id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid()
name                 VARCHAR(200) NOT NULL UNIQUE
description          TEXT
source               VARCHAR(50) NOT NULL DEFAULT 'luxalgo'
strategy_type        VARCHAR(50)
default_profile_json JSONB       -- config base para estrategias derivadas
typical_metrics_json JSONB       -- {"win_rate_range": "70-85%", "pf_range": "2.0-4.0"}
active               BOOLEAN     NOT NULL DEFAULT true
created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
created_by           VARCHAR(100)
```

---

## 13. webhook_deliveries

```sql
id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid()
strategy_decision_id    UUID        NOT NULL REFERENCES strategy_decisions(id)
strategy_id             VARCHAR(100) NOT NULL
destination             VARCHAR(50) NOT NULL DEFAULT 'traderspost'
url_masked              VARCHAR(200)    -- URL con token enmascarado
payload_json            JSONB       NOT NULL
http_status             INTEGER
response_body           TEXT
status                  VARCHAR(20) NOT NULL
-- "DRY_RUN", "SENT", "FAILED", "RETRYING"

attempt_count           INTEGER     NOT NULL DEFAULT 1
latency_ms              INTEGER
delivered_at            TIMESTAMPTZ
error_message           TEXT
created_at              TIMESTAMPTZ NOT NULL DEFAULT now()

INDEX ON webhook_deliveries(strategy_id)
INDEX ON webhook_deliveries(status)
INDEX ON webhook_deliveries(created_at DESC)
```

---

## 14. conflict_logs

```sql
id              UUID        PRIMARY KEY DEFAULT gen_random_uuid()
symbol          VARCHAR(50) NOT NULL
signal_a_id     UUID        NOT NULL REFERENCES normalized_signals(id)
signal_b_id     UUID        REFERENCES normalized_signals(id)
conflict_type   VARCHAR(50) NOT NULL
signal_a_score  INTEGER
signal_b_score  INTEGER
resolution      VARCHAR(50) NOT NULL
resolution_reason TEXT
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
```

---

## 15. audit_logs

Inmutable. Escrito por audit_service en cada cambio de configuración.

```sql
id              UUID        PRIMARY KEY DEFAULT gen_random_uuid()
actor           VARCHAR(100) NOT NULL   -- username o "system"
action          VARCHAR(50) NOT NULL
-- CREATE, UPDATE, DELETE, STATUS_CHANGE, ENABLE, DISABLE,
-- PAUSE, RESUME, QUARANTINE, RETIRE, FLATTEN, LOCK, UNLOCK,
-- TOKEN_GENERATED, WEBHOOK_BLOCKED, GLOBAL_MODE_CHANGE, CLONE

object_type     VARCHAR(50) NOT NULL
-- Strategy, StrategyProfile, AssetProfile, GlobalProfile,
-- SymbolMap, GlobalSetting, PositionState, System

object_id       VARCHAR(100)
old_value_json  JSONB
new_value_json  JSONB
reason          TEXT
ip_address      VARCHAR(45)
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()

INDEX ON audit_logs(actor)
INDEX ON audit_logs(object_type, object_id)
INDEX ON audit_logs(created_at DESC)
```

---

## 16. global_settings

Key-value para configuraciones que no encajan en global_profile.

```sql
id          UUID        PRIMARY KEY DEFAULT gen_random_uuid()
key         VARCHAR(100) NOT NULL UNIQUE
value_json  JSONB       NOT NULL
description TEXT
updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
updated_by  VARCHAR(100)
```

Registros iniciales:
```text
market_data_provider         "ninja_trader_bridge"
ntbridge_heartbeat_max_age   60
market_data_fallback_enabled false
conflict_resolution_mode     "score"
score_tie_action             "reject_both"
symbol_rollover_alert_days   7
```

---

## 17. economic_events (cache de noticias)

```sql
id              UUID        PRIMARY KEY DEFAULT gen_random_uuid()
event_datetime  TIMESTAMPTZ NOT NULL
title           VARCHAR(200) NOT NULL
currency        VARCHAR(10)
impact          VARCHAR(10) NOT NULL    -- "high", "medium", "low"
source          VARCHAR(50)             -- "forexfactory"
fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()

INDEX ON economic_events(event_datetime)
INDEX ON economic_events(impact)
```

---

## Diagrama de relaciones

```text
raw_signals
    └──► normalized_signals (ticker_received + symbol mapeado)
              └──► strategy_decisions (pipeline_execution, sl_price)
                        └──► webhook_deliveries

strategies
    ├── luxalgo_metrics_json (BT referencia)
    ├── template_id → strategy_templates
    ├──► strategy_profiles (pipeline_config_json)
    └──► strategy_performance (métricas reales)

global_profile ──── hereda ───▶ asset_profiles ──── hereda ───▶ strategy_profiles
(base global)       (por activo)                    (por estrategia)

symbol_maps: "MES" → "MESU2025" (búsqueda directa, sin prefijos)
asset_profiles: "MES" → session_config, pine_script_config, sl_atr_multiplier
market_data_status: "MES" → is_active, last_atr_5m, heartbeat_age
position_states: (account, symbol) → state, state_source="estimated"
audit_logs: inmutable, todo cambio de config queda registrado
```
