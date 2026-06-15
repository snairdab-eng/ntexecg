# NTEXECG — Contrato Técnico v1.0

---

## 1. Objetivo del sistema

Construir **NTEXECG**, un gateway intermedio de señales de trading que recibe señales externas de LuxAlgo, las evalúa mediante un pipeline de filtros modulares ordenados, agrega Stop Loss dinámico basado en ATR, y reenvía únicamente las señales aprobadas hacia TradersPost con el SL incluido.

NTEXECG no genera señales. No decide qué estrategia usar. No administra el portafolio. Su responsabilidad es filtrar señales de baja calidad, agregar componentes de riesgo y mejorar la probabilidad de éxito de las señales que ya llegan con cierta probabilidad a favor.

**NTEXECG no depende de ningún broker específico para su funcionamiento.**

---

## 2. Principio central

Las señales externas ya tienen cierta probabilidad a favor. NTEXECG no reinventa esas señales. Elimina señales en condiciones desfavorables, agrega SL obligatorio, y deja que LuxAlgo administre las salidas en ganancia (Builtin-Exits).

El resultado esperado no es operar más. Es operar mejor.

---

## 3. Ecosistema completo

```text
┌──────────────────────────────────────────────────────────────────┐
│                          LUXALGO                                │
│                   (TradingView — Backtesting AI)                │
│                                                                  │
│  Operador busca estrategia → anota métricas BT                  │
│  Configura alerta con ticker MANUAL en JSON                     │
│  Dispara señal a NTEXECG (fire & forget)                        │
└─────────────────────────────┬────────────────────────────────────┘
                              │ POST /webhooks/luxalgo/{strategy_id}?token={secret}
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                          NTEXECG                                │
│                   Ubuntu Server 24.04 LTS                       │
│                                                                  │
│  Pipeline de 5 niveles (fail-fast)                             │
│  SL obligatorio por ATR                                         │
│  Datos de mercado desde NTRADER (tiempo real)                   │
│  Interfaz web de administración                                 │
└─────────────────────────────┬────────────────────────────────────┘
                              │ POST webhook con SL incluido
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                        TRADERSPOST                              │
│                                                                  │
│  Operador crea estrategia manualmente (mismo nombre LuxAlgo)    │
│  Suscribe a cuentas: Paper x6 / Tradovate / Apex / IBKR / etc. │
│  TradersPost rutea UNA señal a TODAS las suscripciones          │
└────────┬─────────────────┬──────────────────┬────────────────────┘
         │                 │                  │
         ▼                 ▼                  ▼
    Paper ✅         Tradovate ✅          Apex ✅ / IBKR ✅
                         │
                         ▼
                  NinjaTrader App
             (visualización de órdenes)
```

---

## 4. Infraestructura — 3 servidores

### NTRADER — Windows Server 2025

```text
Rol:    Fuente de datos de mercado en tiempo real
Red:    LAN compartida con NTEXECG
24/7:   Sí, debe estar activo siempre que se opere

Software:
  NinjaTrader Desktop (conectado a Tradovate — feed CME)
  NTraderExecutionBridge.cs (en cada chart activo)

Exporta cada 10 segundos a C:\NTraderSystem\bridge\out\:
  bars_{symbol}_5m.json    (300 barras OHLCV)
  bars_{symbol}_15m.json   (200 barras)
  bars_{symbol}_1h.json    (250 barras)
  bars_{symbol}_4h.json    (90 barras)
  heartbeat_{symbol}.json  (cada 15 segundos)

Comparte: \\NTRADER\bridge (Samba/SMB) → NTEXECG lo monta
```

### NTEXECG — Ubuntu Server 24.04 LTS

```text
Rol:    Gateway de señales 24/7
Red:    LAN compartida con NTRADER

Software:
  Docker Engine + Docker Compose
  Nginx + Certbot (HTTPS)
  UFW + Fail2ban
  cifs-utils (para montar Samba)

Monta: //NTRADER/bridge → /mnt/ntbridge (read-only, LAN directa)
Recibe: webhooks reales de LuxAlgo
Envía:  señales aprobadas con SL a TradersPost
```

### NTDEV — Windows Server 2025 (sitio remoto)

```text
Rol:    Desarrollo exclusivo
Red:    Sitio remoto, conectado via VPN a NTRADER y NTEXECG

Software:
  VS Code + Claude Code (Max 5x)
  Docker Desktop (WSL2 backend)
  Git, Python 3.12+

Datos de mercado en NTDEV:
  YfinanceProvider (local, delayed ~15min)
  NO monta \\NTRADER\bridge (inestable via VPN)
  MARKET_DATA_PROVIDER=yfinance en .env de desarrollo
```

### Topología de red

```text
┌─────────────────────────────────────────────────────┐
│                   RED LOCAL (LAN)                   │
│                                                     │
│  ┌──────────────────┐     ┌──────────────────────┐  │
│  │     NTRADER      │     │       NTEXECG        │  │
│  │  Windows 2025    │─LAN─▶  Ubuntu 24.04        │  │
│  │  NinjaTrader     │Samba│  Docker + Nginx + PG  │  │
│  └──────────────────┘     └──────────────────────┘  │
└──────────────────────────────────────┬──────────────┘
                                       │ VPN
                            ┌──────────┴──────────┐
                            │        NTDEV        │
                            │  Windows 2025       │
                            │  VS Code + Claude   │
                            │  (sitio remoto)     │
                            └─────────────────────┘
```

---

## 5. Payload entrante de LuxAlgo

El operador configura manualmente el ticker en el JSON de la alerta.

```json
{
    "ticker":    "MJY",
    "action":    "[[strategy_order_action]]",
    "sentiment": "[[strategy_market_position]]",
    "quantity":  "1",
    "price":     "[[strategy_order_price]]",
    "time":      "[[timenow]]",
    "interval":  "[[timeframe]]"
}
```

Reglas críticas:

```text
1. "ticker" es configurado MANUALMENTE por el operador en LuxAlgo.
   NTEXECG nunca transforma ni infiere el ticker.
   Lo que llega es exactamente lo que el operador escribió.

2. "strategy_id" NO viene en el payload.
   Se extrae del URL: POST /webhooks/luxalgo/{strategy_id}

3. Cada estrategia LuxAlgo tiene su propio webhook URL en NTEXECG.
   Una señal de MES con "Confirmation Normal" y otra con "Contrarian Any"
   son estrategias completamente independientes aunque sea el mismo activo.

4. sentiment="flat" → cierre de posición (Builtin-Exit) → action=exit.

5. sentiment="short"/"long" estando en posición opuesta → reversa.
   NTEXECG la separa: exit primero + evaluar nueva entrada independientemente.

6. quantity, price son strings → se castean a int y float.

7. interval="5" → timeframe normalizado a "5m".
```

Ejemplos reales:

```json
{"ticker": "MES",  "action": "buy",  "sentiment": "long",  ...}
{"ticker": "MNQ",  "action": "sell", "sentiment": "short", ...}
{"ticker": "MJY",  "action": "buy",  "sentiment": "long",  ...}
{"ticker": "6J",   "action": "sell", "sentiment": "short", ...}
{"ticker": "MGC",  "action": "buy",  "sentiment": "long",  ...}
{"ticker": "MES",  "action": "sell", "sentiment": "flat",  ...}
```

---

## 6. Symbol Mapper

Traduce el ticker recibido al contrato vigente para TradersPost.
Búsqueda directa en tabla. Sin lógica de prefijos. Sin transformaciones.

```text
ticker_received  mapped_symbol  exchange  tipo            pine_script_config
MES              MESU2025       CME       futures_micro   "ticker": "MES"
MNQ              MNQU2025       CME       futures_micro   "ticker": "MNQ"
MYM              MYMU2025       CBOT      futures_micro   "ticker": "MYM"
M2K              M2KU2025       CME       futures_micro   "ticker": "M2K"
MGC              MGCQ2025       COMEX     futures_micro   "ticker": "MGC"
MJY              MJYU2025       CME       futures_micro   "ticker": "MJY"
M6E              M6EU2025       CME       futures_micro   "ticker": "M6E"
6J               6JU2025        CME       futures_large   "ticker": "6J"
6E               6EU2025        CME       futures_large   "ticker": "6E"
```

Nota: El prefijo M no es una regla universal de CME.
El Micro Yen es MJY (no M6J — ese símbolo no existe en CME).
La tabla es la única fuente de verdad. Si no existe → BLOCK.

La columna `pine_script_config` se muestra en UI al crear una estrategia
para que el operador sepa exactamente qué ticker escribir en LuxAlgo.

---

## 7. Comportamiento de Builtin-Exits de LuxAlgo

```text
Caso 1 — Salida limpia:
  Posición LONG abierta
  LuxAlgo detecta debilidad → envía sentiment="flat"
  NTEXECG: action=exit → cierra LONG → queda FLAT
  NO abre SHORT automáticamente

Caso 2 — Reversa implícita:
  Posición LONG abierta
  Condiciones SHORT se cumplen → LuxAlgo envía sentiment="short"
  NTEXECG separa internamente:
    Paso 1: Cerrar LONG (prioridad máxima, siempre)
    Paso 2: Evaluar entrada SHORT como señal independiente
    Si falla filtros: solo se ejecuta el cierre

IMPORTANTE: Builtin-Exits puede llegar tarde en condiciones extremas.
En backtesting de LuxAlgo se han visto pérdidas del 50%+ sin SL externo.
NTEXECG DEBE agregar SL basado en ATR en cada señal de entrada.
Esta es la protección más importante del sistema.
```

---

## 8. Pipeline de filtros modulares (FAIL-FAST)

Los filtros se evalúan en orden estricto. Si un nivel falla, se detiene
inmediatamente. No se evalúan niveles posteriores.

```text
═══════════════════════════════════════════════════════════════
NIVEL 1 — VALIDACIÓN DEL SISTEMA (binario, sin score)
═══════════════════════════════════════════════════════════════
Falla cualquiera → BLOCK inmediato

1.1 Modo global del sistema
    PAUSED / FLATTEN_ONLY → BLOCK: system_mode
    Excepción: exits siempre pasan

1.2 Estado de la estrategia
    candidate   → QUEUE_FOR_REVIEW
    retired / quarantined → BLOCK: strategy_{status}
    paused + entrada → BLOCK: strategy_paused
    paused + salida  → continuar

1.3 Deduplicación
    Hash idéntico en últimos 60 segundos → IGNORE_DUPLICATE

1.4 Symbol Mapper
    ticker_received no existe en tabla → BLOCK: symbol_not_mapped

1.5 Símbolo permitido
    ticker_raw no en allowed_symbols → BLOCK: symbol_not_allowed

1.6 Datos de mercado activos
    heartbeat_{symbol}.json > 60 segundos → BLOCK: market_data_not_active
    Excepción: exits se permiten aunque NT esté inactivo

═══════════════════════════════════════════════════════════════
NIVEL 2 — CONTEXTO TEMPORAL (binario, sin score)
═══════════════════════════════════════════════════════════════
Falla → BLOCK entradas / evaluar allow_exits_outside para salidas

2.1 Día de la semana
    Cada activo tiene su propio calendario:
    MES/MNQ/MYM/M2K/MGC: Lun-Vie
    MJY/M6E/6J/6E:        Dom 18:00 ET → Vie 17:00 ET (casi 24h)
    → BLOCK: day_not_enabled

2.2 Horario de sesión por activo
    Configurable en AssetProfile:
    MES/MNQ:  09:30-15:45 ET
    MYM/M2K:  09:30-15:45 ET
    MGC:      08:20-13:30 ET (pit)
    MJY/M6E:  18:00-17:00 ET (continuo)
    6J/6E:    18:00-17:00 ET (continuo)
    → Entrada fuera: BLOCK: outside_trading_window
    → Salida fuera: evaluar allow_exits_outside_window

2.3 Ventana de noticias de alto impacto
    NFP, CPI, FOMC, decisiones de tasas, etc.
    Ventana: ±news_window_minutes configurable (default 30min)
    → BLOCK: news_window_active

═══════════════════════════════════════════════════════════════
NIVEL 3 — RIESGO (binario, sin score)
═══════════════════════════════════════════════════════════════
Falla → BLOCK

3.1 Daily loss stop
    Pérdida del día >= límite → BLOCK: daily_loss_stop_reached

3.2 Max open positions
    Posiciones >= máximo → BLOCK: max_positions_reached

3.3 Position state
    LOCKED  → BLOCK: position_locked
    UNKNOWN + entrada → BLOCK: position_unknown
    FLAT + exit → BLOCK: no_position_to_exit

═══════════════════════════════════════════════════════════════
NIVEL 4 — SCORE DE CALIDAD (solo entradas)
═══════════════════════════════════════════════════════════════
Solo si pasó Niveles 1-3. Las salidas saltan al Nivel 5.
Score < score_minimum → BLOCK: score_below_threshold

Filtros (NO redundantes con LuxAlgo que ya evalúa momentum/tendencia):
  Volumen relativo     (peso configurable, default 30pts) — Fase 5
  ATR normalizado      (peso configurable, default 25pts) — Fase 5
  VWAP position        (peso configurable, default 25pts) — Fase 5
  Time of day quality  (peso configurable, default 20pts) — Fase 5
  Régimen HMM          (binario dentro del score)         — Fase 6

En MVP (Fase 1): score placeholder = 100. Estructura lista, filtros en Fase 5.

═══════════════════════════════════════════════════════════════
NIVEL 5 — SL/TP CALCULATION (solo entradas aprobadas)
═══════════════════════════════════════════════════════════════
SL OBLIGATORIO. Si ATR no disponible → BLOCK: atr_calculation_failed

SL basado en ATR:
  LONG:  sl_price = entry_price - (ATR × sl_atr_multiplier)
  SHORT: sl_price = entry_price + (ATR × sl_atr_multiplier)
  sl_atr_multiplier: configurable por activo/estrategia desde UI

TP: Via Builtin-Exits de LuxAlgo (default).
  tp_atr_multiplier: preparado en DB pero no activo por defecto.
  Se evalúa con datos reales de paper después de 30+ días.

Payload enviado a TradersPost:
{
  "ticker":      "MESU2025",
  "action":      "sell",
  "sentiment":   "short",
  "signalPrice": 5500.00,
  "quantity":    1,
  "stopLoss":    {"type": "stop", "price": 5484.00},
  "extras":      {"strategy_id": "...", "ntexecg_score": 78, "atr": 8.0}
}
```

---

## 9. Datos de mercado

### Proveedor actual — NinjaTrader Bridge

```text
NinjaTrader Desktop en NTRADER exporta OHLCV via bridge.
NTEXECG monta \\NTRADER\bridge → /mnt/ntbridge (LAN, sin VPN).
NinjaTraderBridgeProvider lee archivos JSON, calcula ATR con pandas-ta.
Datos en tiempo real del feed de Tradovate. Gratis.
Riesgo: si NinjaTrader se cae → check 1.6 bloquea entradas.
```

### MarketDataService — abstracción

```text
Cambiar de proveedor = cambiar solo MarketDataService.
FilterPipeline, SLTPCalculator y QualityScorer no cambian.

Providers disponibles:
  NinjaTraderBridgeProvider  ← activo en producción (NTEXECG)
  YfinanceProvider            ← activo en desarrollo (NTDEV)
  TradovateAPIProvider        ← stub, implementar en Fase 5+
  DatabentoProvider           ← stub, implementar en Fase 5+
```

### Roadmap de migración

```text
ACTUAL (todas las fases de validación):
  NinjaTrader Bridge → LAN → NTEXECG
  Gratis, tiempo real, depende de NTRADER activo

CUANDO SE GENEREN GANANCIAS (Fase 5+):
  Migrar a proveedor independiente:
  Opción A: Tradovate API (gratis, incluida en cuenta)
  Opción B: Databento (~$50-150/mes, $125 créditos gratis)
  Migración transparente: solo cambia MARKET_DATA_PROVIDER en .env
```

---

## 10. Flujo de alta de una estrategia

```text
PASO 1 — LUXALGO
  Buscar estrategia en AI Backtesting
  Anotar: Win Rate, PF, Max DD, # trades

PASO 2 — TRADERSPOST
  Nueva estrategia → mismo nombre que en LuxAlgo → Save
  Nueva suscripción → broker (paper/live) → Create
  Clic "Webhook" → copiar URL

PASO 3 — NTEXECG (~2 minutos)
  Nueva estrategia → mismo nombre
  Ingresar métricas BT de LuxAlgo
  Pegar URL de TradersPost
  Seleccionar activo → UI muestra ticker exacto para LuxAlgo
  Generar token → copiar URL de NTEXECG

PASO 4 — LUXALGO (regreso)
  Alertas → New
  Configurar JSON con ticker EXACTO (el que muestra NTEXECG)
  Pegar URL de NTEXECG en campo Webhook
  Create → activar alerta

PASO 5 — TRADERSPOST
  Habilitar suscripción
```

---

## 11. Gestión del ciclo de vida de estrategias

### Principio fundamental

**El operador toma TODAS las decisiones del ciclo de vida.**
NTEXECG nunca degrada, pausa ni retira estrategias automáticamente.
El sistema muestra métricas y alertas informativas. El operador decide.

### Estados de estrategia

```text
candidate     → Auto-creada al recibir señal desconocida. No ejecuta.
shadow        → Observa y logea sin ejecutar. Valida lógica.
paper         → Ejecuta en cuentas paper de TradersPost.
micro         → Ejecuta con cantidad mínima en cuenta real.
limited_live  → Ejecuta con límites diarios reducidos.
live          → Ejecución completa según perfil.
paused        → Entradas bloqueadas. Salidas permitidas.
quarantined   → Todo bloqueado. Requiere revisión manual.
retired       → Desactivada permanentemente.
```

### Herramienta de escala — Clonación

```text
[Clonar estrategia] desde UI:
  Copia toda la config del perfil
  Cambia solo: strategy_id, ticker, traderspost_webhook_url
  Nueva estrategia arranca en candidate
  Zero fricción para probar misma estrategia en otro activo
```

### Alertas informativas (no ejecutoras)

```text
⚠️ Win rate real 51% vs BT 83%     (información, no acción)
⚠️ 5 pérdidas consecutivas en paper (información, no acción)
El operador decide si pausar, ajustar o continuar.
```

---

## 12. Position State

```text
MVP: Estado ESTIMADO basado en señales enviadas a TradersPost.
     NTEXECG no recibe confirmación de ejecución de los brokers.
     La UI lo indica explícitamente en todas las vistas.

Futuro: Si se integra confirmación via NTraderExecutionBridge.cs:
     Position State CONFIRMADO para cuentas NinjaTrader.
     Estimado para Apex, IBKR y otras cuentas sin bridge.
```

---

## 13. Stack técnico

```text
Backend:        Python 3.12, FastAPI, Pydantic v2
                SQLAlchemy 2.x async, Alembic, asyncpg
UI:             Jinja2 + HTMX + Tailwind CSS + Alpine.js
Base de datos:  PostgreSQL 16
Datos mercado:  NinjaTraderBridgeProvider (producción)
                YfinanceProvider (desarrollo)
Indicadores:    pandas-ta (ATR, VWAP, volumen relativo)
HMM (Fase 6):   hmmlearn
Scheduler:      APScheduler (heartbeat monitor, forced close,
                rollover alerts, backup)
HTTP client:    httpx async (hacia TradersPost)
Logging:        loguru
Testing:        pytest + pytest-asyncio
Contenedores:   Docker Compose
Proxy:          Nginx + Certbot (HTTPS)
```

---

## 14. Reglas no negociables

```text
1.  Interfaz web desde el inicio. Sin excepciones.
2.  Configuración operativa por UI, no por código.
3.  Toda señal auditada desde recepción hasta decisión final.
4.  Toda decisión tiene motivo y desglose de filtros registrados.
5.  Los filtros se evalúan en orden estricto (fail-fast, 5 niveles).
6.  Toda entrada aprobada incluye SL basado en ATR. Sin excepciones.
7.  Las salidas tienen prioridad absoluta sobre entradas.
8.  Las reversas = exit + nueva entrada evaluada independientemente.
9.  El operador toma TODAS las decisiones de ciclo de vida.
    NTEXECG nunca degrada ni pausa estrategias automáticamente.
10. Ninguna estrategia nueva opera live automáticamente.
11. Si el estado es incierto: bloquear entradas, permitir salidas.
12. Paper y live claramente separados en UI y en base de datos.
13. Se puede apagar por estrategia, activo, cuenta o global.
14. strategy_id siempre viene del URL path, nunca del payload.
15. El ticker en el payload es configurado manualmente en LuxAlgo.
    NTEXECG nunca transforma ni infiere el ticker.
16. Symbol Mapper actualizado antes de cada rollover de contratos.
17. sentiment=flat siempre produce action=exit.
18. Position State es estimado en MVP. La UI lo indica explícitamente.
19. Análisis técnico de NTEXECG no repite lo que LuxAlgo ya evalúa.
20. Horario de sesión configurado por activo, no globalmente.
21. Clonación de estrategias disponible desde UI para escalar.
22. UI muestra ticker exacto para LuxAlgo al crear estrategia.
23. NTEXECG no depende de ningún broker para datos de mercado.
24. MarketDataService es abstracción: migrar proveedor no requiere
    cambios en FilterPipeline ni SLTPCalculator.
25. Si NinjaTrader no está activo: BLOCK entradas, PERMIT salidas.
26. NTRADER y NTEXECG están en la misma LAN. El montaje Samba es
    directo, sin VPN. NTDEV accede via VPN solo para administración.
```
