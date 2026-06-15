# NTEXECG — Requerimientos de Interfaz Web v1.0

---

## 1. Objetivo y principios

La interfaz web es el centro de control operativo de NTEXECG. Existe desde la primera fase funcional. Ninguna operación normal requiere editar código, archivos de configuración o acceder directamente a la base de datos.

```text
1.  Todo cambio operativo normal se hace desde UI.
2.  Todo cambio se audita automáticamente.
3.  Toda decisión es visible con desglose por nivel de pipeline.
4.  Toda señal trazable desde recepción hasta TradersPost.
5.  Pausar rápido una estrategia, activo, cuenta o el sistema.
6.  Acciones peligrosas requieren confirmación explícita.
7.  Paper y live visualmente diferenciados (badges de color).
8.  DRY RUN siempre visible y prominente.
9.  Position State indicado como estimado en toda la UI.
10. Alertas de métricas son INFORMATIVAS, nunca ejecutan acciones.
11. Estado del bridge de datos visible en todo momento.
12. Desktop-first. Responsive secundario.
```

---

## 2. Stack

```text
FastAPI + Jinja2     Server-side rendering
HTMX                 Actualizaciones parciales y polling
Tailwind CSS         Estilos (via CDN, sin build step)
Alpine.js            Interactividad mínima (modales, toggles)
```

---

## 3. Indicadores globales (siempre visibles en navbar)

```text
┌──────────────────────────────────────────────────────────────────────┐
│ NTEXECG v1.0 │ Modo: NORMAL ● │ DRY RUN ⚠️ │ Bridge: ● │ 14:32 ET │
└──────────────────────────────────────────────────────────────────────┘
```

Badge de modo:
```text
NORMAL       → Verde
DEFENSIVE    → Amarillo
FLATTEN_ONLY → Naranja
PAUSED       → Rojo
```

Badge DRY RUN: naranja, visible solo cuando dry_run=true. Prominente.

Badge Bridge: ● Verde si todos los símbolos activos / ⚠ Naranja si alguno inactivo / 🔴 Rojo si ninguno activo.

---

## 4. Páginas

### 4.1 Dashboard — `/ui`

**Panel de métricas de hoy:**
```text
┌───────────┬───────────┬───────────┬───────────┐
│ Recibidas │ Aprobadas │ Bloqueadas│  Enviadas │
│    47     │    23     │    24     │    22     │
└───────────┴───────────┴───────────┴───────────┘
┌───────────┬───────────┬───────────┬───────────┐
│Estrategias│  Paper    │   Live    │  Pausadas │
│activas: 5 │    3      │    2      │    1      │
└───────────┴───────────┴───────────┴───────────┘
```

**Estado del bridge (HTMX polling 30s):**
```text
DATOS DE MERCADO — NinjaTrader Bridge (NTRADER)
Symbol  Estado      ATR 5m   ATR 1h   Heartbeat
MES     ● Activo    6.25     8.50     8s
MNQ     ● Activo    22.50    31.00    12s
MJY     ⚠ Inactivo  —        —        185s
```

**Alertas críticas:**
```text
⚠️ NinjaTrader inactivo para MJY — Sin datos desde 3 min
   Entradas bloqueadas. Salidas permitidas.
⚠️ Contrato MGCQ2025 expira en 5 días — Actualizar Symbol Mapper
❌ Delivery fallido hace 2 min — Ver detalle
```

**Acciones rápidas (con modal de confirmación):**
```text
[⏸ Pausar todo]  [🔻 Flatten only]  [▶ Reanudar]
```

**Feed de eventos (HTMX polling 10s):**
```text
14:31:05  APPROVE  luxalgo_ema_mnq  MNQ  sell  score:78  → SENT
14:30:52  BLOCK    luxalgo_rsi_mes  MES  buy   outside_trading_window
14:30:41  APPROVE  luxalgo_ema_mes  MES  buy   score:71  → DRY_RUN
```

---

### 4.2 Estrategias — `/ui/strategies`

Tabla con columnas:
```text
strategy_id | nombre | activo | TF | status badge | modo | BT WR% | Real WR% | enabled
```

Badges de status:
```text
candidate → Gris    shadow → Azul claro    paper → Azul
micro → Naranja claro    limited_live → Naranja    live → Verde
paused → Amarillo    quarantined → Rojo    retired → Gris oscuro
```

Selección múltiple + acciones en lote:
```text
[☐ Seleccionar] → [Con seleccionadas ▼] → Pausar / Shadow / Retirar
```

---

### 4.3 Formulario de nueva estrategia

Orden empatado con el flujo real del operador (TradersPost primero):

**Sección 1 — Identidad**
```text
Nombre: [________________________________] (mismo que en TradersPost)
Descripción: [___________________________] (opcional)
Fuente:       [LuxAlgo ▼]
Activo base:  [Seleccionar activo ▼]
  MES — Micro E-mini S&P 500
  MNQ — Micro E-mini Nasdaq-100
  MYM — Micro E-mini Dow Jones
  M2K — Micro E-mini Russell 2000
  MGC — Micro Gold
  MJY — Micro JPY/USD
  M6E — Micro EUR/USD
  6J  — JPY/USD full size
  6E  — EUR/USD full size
  [+ Agregar activo]
Timeframe:    [5m ▼]
Tipo:         [Trend Following ▼]
```

**→ Al seleccionar activo, aparece automáticamente:**
```text
┌──────────────────────────────────────────────────────┐
│ 📋 Ticker para configurar en LuxAlgo                │
│   "ticker": "MJY"                                   │
│   [Copiar]  Usa este valor en el JSON de la alerta  │
└──────────────────────────────────────────────────────┘
```

**Sección 2 — Métricas LuxAlgo BT**
```text
Win Rate BT:      [91.35] %
Profit Factor BT: [4.68]
Max Drawdown BT:  [6.83] %
# Trades BT:     [81]
Desde:            [2026-02-13]
```

**Sección 3 — Conexión TradersPost**
```text
TradersPost Webhook URL:
[https://webhooks.traderspost.io/trading/webhook/...]
↑ Copia desde TradersPost → tu estrategia → Webhook

Modo inicial:
(●) Paper    ( ) Shadow    ( ) Micro    ( ) Live
```

**Sección 4 — URL de NTEXECG para LuxAlgo**
```text
[Generar token de webhook]

Una vez generado:
┌──────────────────────────────────────────────────────┐
│ URL para pegar en LuxAlgo (campo Webhook):          │
│ https://ntexecg.tudominio.com/webhooks/luxalgo/     │
│ 6j5m_confirmation_strong_contrarian                  │
│ ?token=abc123xyz                                     │
│ [📋 Copiar URL completa]                             │
└──────────────────────────────────────────────────────┘
⚠️ Token mostrado una sola vez. Guárdalo ahora.
```

**Sección 5 — Config inicial (heredada, editable)**
```text
Horario:   [Heredar de activo MJY ▼] Preview: 18:00-17:00 ET, Dom-Vie
SL mult:   [Heredar activo (2.0x) ▼]
Score mín: [Heredar global (65) ▼]
```

**Pantalla post-guardado:**
```text
✅ Estrategia creada en estado PAPER

1. Ticker para el JSON de LuxAlgo:
   "ticker": "MJY"   [📋 Copiar]

2. URL del webhook para LuxAlgo:
   https://ntexecg.tudominio.com/webhooks/luxalgo/...?token=...
   [📋 Copiar URL]

3. En TradersPost: habilita la suscripción

⚠️ Token mostrado una sola vez.
[Ver perfil completo]    [Crear otra estrategia]
```

---

### 4.4 Perfil de estrategia — `/ui/strategies/{id}`

Tabs:

**General:** nombre, source, activo, TF, tipo, status, modo, traderspost_webhook_url, luxalgo_metrics_json, template origen

**Horario:** timezone, días, entry_start/end, avoid_open/close_minutes, force_flat, allow_overnight, allow_exits_outside

**Activos y cuentas:** allowed_symbols, allowed_accounts, routing_mode

**Filtros (Pipeline):**
```text
Nivel 1 — Validación del sistema   [siempre activo]
Nivel 2 — Temporal                 [configurable]
  Horario: hereda de activo
  Noticias: [±30 min] [Alto ✓] [Medio □] [Bajo □]
Nivel 3 — Riesgo                   [configurable]
  Daily loss stop: [$___]
  Max posiciones:  [1]
Nivel 4 — Score                    [configurable]
  Score mínimo: [65]
  Filtros (activos en Fase 5):
    Volumen relativo  [OFF] peso:[30] umbral:[1.2x]
    ATR normalizado   [OFF] peso:[25] σ:[2.0]
    VWAP position     [OFF] peso:[25]
    Time of day       [OFF] peso:[20]
    Régimen HMM       [OFF] (Fase 6)
Nivel 5 — SL/TP (siempre activo)
  sl_atr_multiplier: [2.0]x
  tp_atr_multiplier: [___] (vacío = Builtin-Exits)
  atr_period: [14]
```

**Exit Policy:** forced_close_time, max_holding_minutes, allow_overnight, break_even_ticks

**Riesgo:** max_trades_day, daily_loss_stop, daily_profit_lock, max_quantity

**Performance (BT vs Real):**
```text
┌──────────────┬─────────────┬──────────────────┐
│ Métrica      │ LuxAlgo BT  │ Real (paper)     │
├──────────────┼─────────────┼──────────────────┤
│ Win Rate     │ 83.81%      │ 61.3% ⚠️         │
│ PF           │ 3.99        │ 1.42 ⚠️          │
│ Max DD       │ 14.32%      │ 8.1% ✅          │
│ # Trades     │ 105 (BT)    │ 23 (paper)       │
├──────────────┴─────────────┴──────────────────┤
│ Señales: 47 recibidas │ 23 aprobadas (48.9%) │
│ Top bloqueo: outside_trading_window (45%)     │
└─────────────────────────────────────────────────┘
⚠️ Datos reales basados en Position State estimado.
[Esta información es referencial. El operador decide.]
```

**Señales recientes:** últimas 50 con link a detalle

**Decisiones recientes:** últimas 50 con desglose

**Auditoría:** historial de cambios de esta estrategia

**Acciones con confirmación:**
```text
[⏸ Pausar]  [▶ Reanudar]  [📋 Paper]  [📊 Micro]
[🔴 Live] ← requiere escribir "CONFIRMAR"
[🔒 Quarantine] ← motivo obligatorio
[🗑 Retirar] ← motivo obligatorio
[📄 Clonar] ← sin confirmación (operación segura)
```

---

### 4.5 Señales — `/ui/signals`

Tabla con filtros: strategy_id, symbol, action, decision, date range.
Paginación: 50 por página.
Color de fila: APPROVE=verde, BLOCK=rojo, IGNORE=gris.

---

### 4.6 Detalle de señal — `/ui/signals/{id}`

```text
TICKER RECIBIDO: MJY    CONTRATO MAPEADO: MJYU2025

RAW PAYLOAD:
{ "ticker": "MJY", "action": "sell", "sentiment": "short", ... }

SEÑAL NORMALIZADA:
strategy_id: 6j5m_confirmation_strong
ticker_received: MJY → MJYU2025
action: sell  |  signal_role: entry_short
price: 148.250  |  qty: 1  |  TF: 5m

PIPELINE DE FILTROS:
Nivel 1 — Sistema        ✅ Pasó (6/6 checks)
  ✓ Modo: NORMAL
  ✓ Estrategia: paper/enabled
  ✓ Deduplicación: señal única
  ✓ Symbol Mapper: MJY → MJYU2025
  ✓ Símbolo permitido
  ✓ Bridge activo: heartbeat 8s

Nivel 2 — Temporal       ✅ Pasó
  ✓ Día: Martes (Dom-Vie habilitado)
  ✓ Horario: 14:31 ET dentro de 18:00-17:00 ET
  ✓ Sin noticias en ±30min

Nivel 3 — Riesgo         ✅ Pasó
  ✓ Daily loss: $0 / $500 límite
  ✓ Posiciones: 0 / 1 máximo

Nivel 4 — Score          ✅ 100/100 ≥ 65 mínimo
  (MVP: score placeholder, filtros activos en Fase 5)

Nivel 5 — SL/TP          ✅ Calculado
  Entry:     148.250 SHORT
  ATR:       0.0045 (14p, 5m) — NinjaTrader Bridge
  SL:        148.259 (+0.009, mult 2.0x)
  TP:        Via Builtin-Exits LuxAlgo

DECISIÓN: APPROVE ✅  Score: 100

DELIVERY TRADERSPOST:
Status: SENT ✅  HTTP: 200  Latencia: 284ms
Payload enviado:
{ "ticker": "MJYU2025", "action": "sell", "sentiment": "short",
  "signalPrice": 148.250, "quantity": 1,
  "stopLoss": {"type": "stop", "price": 148.259} }
```

Si BLOCK, mostrar el nivel que falló y la razón:
```text
Nivel 2 — Temporal       ❌ BLOQUEADO en 2.2
  ✓ Día: Martes
  ✗ Horario: 16:02 ET fuera de 09:30-15:45 ET
  ⏭ Noticias: no evaluado
Nivel 3-5:  ⏭ No evaluados

DECISIÓN: BLOCK — outside_trading_window
```

---

### 4.7 Posiciones — `/ui/positions`

```text
⚠️ Estado estimado basado en señales enviadas.
   Verificar posiciones reales en TradersPost / NinjaTrader.

cuenta    | symbol   | estado~ | dir   | entry    | SL enviado
paper_1   | MJYU2025 | SHORT~  | short | 148.250  | 148.259
tradovate | MJYU2025 | SHORT~  | short | 148.250  | 148.259

~ = estimado, no confirmado por broker
```

Acciones: [🔻 Flatten] [🔒 Lock] [🔓 Unlock] — todas con confirmación.

---

### 4.8 Symbol Mapper — `/ui/symbol-map`

```text
ℹ️ La columna "Pine Script Config" muestra el ticker exacto para LuxAlgo.
   Cada instrumento tiene su propio símbolo en CME.
   Ejemplo: Micro Yen = "MJY" (no "M6J" — ese símbolo no existe en CME).

Pine Script Config  | TV Symbol | Contrato  | Tipo           | Expira   | Activo
"ticker": "MES"    | MES       | MESU2025  | futures_micro  | 19/09/25 | ✅
"ticker": "MNQ"    | MNQ       | MNQU2025  | futures_micro  | 19/09/25 | ✅
"ticker": "MYM"    | MYM       | MYMU2025  | futures_micro  | 19/09/25 | ✅
"ticker": "M2K"    | M2K       | M2KU2025  | futures_micro  | 19/09/25 | ✅
"ticker": "MGC"    | MGC       | MGCQ2025  | futures_micro  | 27/08/25 | ✅ ⚠️5d
"ticker": "MJY"    | MJY       | MJYU2025  | futures_micro  | 15/09/25 | ✅
"ticker": "M6E"    | M6E       | M6EU2025  | futures_micro  | 15/09/25 | ✅
"ticker": "6J"     | 6J        | 6JU2025   | futures_large  | 15/09/25 | ✅
"ticker": "6E"     | 6E        | 6EU2025   | futures_large  | 15/09/25 | ✅
```

---

### 4.9 Asset Profiles — `/ui/assets`

```text
Symbol | Pine Script        | Sesión                   | SL Mult | Score
MES    | "ticker": "MES"   | 09:30-15:45 ET Lun-Vie  | 2.0x    | 65
MNQ    | "ticker": "MNQ"   | 09:30-15:45 ET Lun-Vie  | 2.0x    | 70
MGC    | "ticker": "MGC"   | 08:20-13:30 ET Lun-Vie  | 2.0x    | 65
MJY    | "ticker": "MJY"   | 18:00-17:00 ET Dom-Vie  | 2.0x    | 65
6J     | "ticker": "6J"    | 18:00-17:00 ET Dom-Vie  | 2.0x    | 65
```

---

### 4.10 Strategy Templates — `/ui/strategy-templates`

Lista de plantillas. Al crear estrategia desde template, el formulario se precarga con la config del template.

---

### 4.11 Settings — `/ui/settings`

Secciones:
- Sistema: global_mode, dry_run, traderspost_enabled, timezone
- Riesgo global: max_positions, daily_loss_stop, profit_lock
- Pipeline: news_window_minutes, score_minimum
- TradersPost: retry_attempts, timeout, entry_signal_timeout
- Datos de mercado: provider activo, bridge path, heartbeat_max_age

Cambiar provider requiere confirmación con descripción del impacto.

---

### 4.12 Audit — `/ui/audit`

Tabla: fecha/hora, actor, acción, objeto, valor anterior, nuevo, motivo.
Filtros + Exportar CSV.

---

## 5. Acciones que requieren confirmación (modal)

```text
- Cambiar estrategia a live        ← escribir "CONFIRMAR"
- Desactivar dry_run               ← escribir "CONFIRMAR"
- Activar traderspost_enabled      ← escribir "CONFIRMAR"
- Quarantine estrategia            ← motivo obligatorio
- Retirar estrategia               ← motivo obligatorio
- Flatten global                   ← confirmación simple
- Pausar todo el sistema           ← confirmación simple
- Cambiar provider de datos        ← confirmación con impacto
- Cambiar daily loss stop          ← confirmación simple
```

---

## 6. MVP mínimo (Fase 1)

```text
✅ Layout base con todos los indicadores globales
✅ Dashboard con bridge status, métricas y feed de eventos
✅ Lista de estrategias con badges
✅ Formulario de nueva estrategia (flujo completo con ticker hint)
✅ Clonar estrategia
✅ Strategy Templates (básico)
✅ Cambiar status con confirmación y AuditLog
✅ Acciones en lote
✅ Lista y detalle de señales con desglose de pipeline
✅ SL calculado visible en señales APPROVE
✅ Posiciones con estado estimado
✅ Symbol Mapper (formato correcto, pine_script_config prominente)
✅ Asset Profiles (básico)
✅ Settings completo
✅ Audit log
```
