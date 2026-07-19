# Dashboard — Evaluación lógica y prompts de arreglo · 2026-07-07

> Evaluación del arquitecto saliente (Fable) para que **Opus 4.8** implemente.
> Patrón de referencia: pestaña Riesgo (unidades FX del Symbol Mapper,
> identidad del dato visible, fail-closed, tests). La demo está ACTIVA:
> el Dashboard es ahora la pantalla de vigilancia diaria del operador.

## A. Evaluación (verificado en producción 2026-07-07)

**Lo sano (conservar):**
- KPIs del rango funcionan (7d: 46 recibidas · 45 decisiones · 15 aprobadas ·
  33.3% · 34 enviadas · 0 fallidas) y el selector {hoy,7,14,30,90} también.
- Charts (flujo diario, outcomes, niveles, motivos) + tabla por estrategia
  (ids canónicos NX-24) + decisiones recientes: conectados a datos reales.
- Partials HTMX load-bearing (bridge-badge en el navbar) — NO tocar contratos.

**Lo muerto / inservible (tabla "Datos de mercado — NinjaTrader Bridge"):**
1. **Columnas "ATR 1H" y "HEARTBEAT" SIEMPRE "—"**: `HeartbeatMonitor`
   (`app/core/scheduler.py::_check`) solo upserta `is_active` +
   `last_atr_5m`; `last_atr_1h` y `heartbeat_age_seconds` existen en el
   modelo/tabla desde el esquema inicial pero NADIE los escribe. Columnas
   muertas renderizadas en `partials/bridge_status.html`.
2. **FX muestra "ATR 5M 0.00"** (6E, 6J, M6E, MJY): el ATR real es ~0.0004
   (unidad de precio) y el template redondea a 2 decimales → un "0.00" que
   parece bridge roto. Misma clase de bug FX que ya se arregló en Riesgo
   (P1-2/R-obs-4: FX se muestra en TICKS con el tick_size del Symbol
   Mapper, nunca "0 pts").
3. **16 filas donde ~8 aportan**: cada micro duplica el dato del padre
   (MES≡ES 5.70, MNQ≡NQ 42.91, M2K≡RTY…) porque comparten archivo de
   bridge (probe_cache). Ruido visual sin información nueva.

**Lo que FALTA para vigilar la demo (recomendaciones de utilidad):**
4. **Posiciones abiertas** — el dato #1 de un gateway armado y no está en
   el Dashboard (existe PositionService/pestaña Posiciones).
5. **Últimas entregas con su bracket** — la decisión reciente muestra
   APPROVE/BLOCK pero no si se ENVIÓ ni con qué stopLoss/takeProfit; el
   checkpoint de "la primera entrada con bracket nuevo" se vigila a mano.
6. **Deriva global del Puente** — un contador "N estrategias difieren de su
   estudio" (ya existe `deriva_estudio`; hoy hay que abrir Riesgo una por
   una).
7. **Kill-switch por capas visible** — env TRADERSPOST_ENABLED / global /
   por estrategia armada: hoy se infiere abriendo cada ficha.

---

## B. 📋 PROMPT PARA OPUS 4.8 — LOTE DASH-1: tabla del bridge sana (datos reales, unidades reales)

Eres el implementador de NTEXECG (FastAPI + Jinja2/HTMX; solo paper/demo).
Archivos: `app/core/scheduler.py`, `app/web/routes_dashboard.py`,
`app/templates/partials/bridge_status.html`, tests. NO commit/push.
Verifica con `.venv\Scripts\python.exe -m pytest -q` (si se cuelga:
`-o faulthandler_timeout=300 --timeout=600`).

Contexto: la tabla "Datos de mercado" del Dashboard tiene dos columnas
muertas (ATR 1H, HEARTBEAT — el HeartbeatMonitor jamás las escribe aunque
el modelo MarketDataStatus las tiene), muestra "0.00" para FX (redondeo a
2 decimales de ATRs de ~0.0004) y duplica micro/padre (16 filas, 8 datos).

Tareas:
1. **Llenar `last_atr_1h`**: en `HeartbeatMonitor._check`, cuando el
   símbolo esté activo, además de `get_atr(data_symbol, "5m", 14)` pide
   `get_atr(data_symbol, "1h", 14)` (verifica ANTES en
   `app/services/market_data_service.py` que el provider del bridge sirve
   timeframe "1h" — los HOLC 1h existen; si el bridge NO exporta bars_1h
   en producción, la columna se ELIMINA en vez de fingir). Persistir vía
   `upsert_market_data_status(..., last_atr_1h=...)` (mismo probe_cache
   para no leer dos veces por ciclo).
2. **Llenar `heartbeat_age_seconds`**: inspecciona el provider del bridge
   (NinjaTraderBridgeProvider) — ya valida un heartbeat con
   `heartbeat_max_age`; expón la EDAD real (segundos desde el último
   heartbeat) con un método del provider y persístela. Si el heartbeat es
   GLOBAL del bridge (no por símbolo), muéstralo UNA vez en la cabecera de
   la tabla ("Heartbeat: hace Xs") y ELIMINA la columna por fila — no
   repitas 16 veces el mismo número.
3. **Unidades FX** (regla P1-2/R-obs-4, fuente única Symbol Mapper): en el
   partial, si `tick_size` del símbolo existe y el ATR en precio es
   < 0.01 (o tick_size < 0.01), muestra el ATR en TICKS:
   "8 ticks (0.0004)" — nunca "0.00". Reusa el patrón de
   `routes_riesgo._fmt_unidad` (no dupliques: extrae helper compartido si
   hace falta).
4. **Agrupar micro/padre**: una fila por símbolo de DATOS (ES, NQ, RTY,
   GC, CL, 6E, 6J, YM) con badge de los tradeables que respalda
   ("ES → MES"); o si prefieres conservar filas por tradeable, marca las
   micro con "· datos de <padre>" en gris. Elige lo más legible; el
   criterio: cero números repetidos sin contexto.
5. Tests (`tests/test_dashboard_unificado.py` o nuevo): monitor persiste
   atr_1h y heartbeat_age (mock provider); partial FX en ticks (6J con
   tick_size 5e-7 y ATR 3.6e-5 → "72 ticks", jamás "0.00"); sin edad →
   "—"; agrupación sin duplicados.

Invariantes: los partials `bridge-badge` y `bridge-status` mantienen sus
URLs y contratos HTMX (base.html:72 es load-bearing); el monitor nunca
lanza (errores → log + campo None); solo paper/demo. Al final: `git diff
--stat` + "LISTO PARA COMMIT" si la suite queda verde.

---

## C. 📋 PROMPT PARA OPUS 4.8 — LOTE DASH-2: fila de vigilancia de la demo

Mismo marco operativo (hazlo DESPUÉS de DASH-1). La demo está activa: el
Dashboard debe contestar de un vistazo "¿qué está abierto, qué se envió,
con qué bracket, y hay algo desalineado?".

Tareas:
1. **Tarjeta "Posiciones abiertas"** en la fila operacional: cuenta + lista
   compacta (símbolo · lado · qty · desde cuándo) desde el mismo servicio
   que usa la pestaña Posiciones (PositionService / modelo de posiciones —
   inspecciona `app/services/position_service.py`; NO dupliques lógica,
   reusa el repositorio). Link a /ui/positions.
2. **"Últimas entregas" con bracket**: tabla corta (5) de WebhookDelivery
   SENT/FAILED del rango con: hora, estrategia, acción, y del payload el
   `stopLoss.stopPrice` y `takeProfit.limitPrice` si existen (el payload
   se persiste en la delivery — verifica el campo real del modelo
   WebhookDelivery; si no se persiste completo, muestra lo que haya y NO
   inventes). Esto convierte el checkpoint "la entrada estrenó el bracket
   nuevo" en un vistazo.
3. **Badge de deriva global**: un contador en la fila operacional —
   "Estudios: N aplicadas · M difieren · K sin aplicar" — computado con
   `routes_riesgo.deriva_estudio` + `_activacion_json` sobre el manifest
   (mismo patrón que el badge por estrategia del Puente). CUIDADO con el
   costo: cachea en memoria con TTL corto (p. ej. 60s) o computa solo
   claves con estudio; nada de recomputos pesados por request.
4. **Kill-switch por capas visible**: chips "env ✓/✗ · global ✓/✗ ·
   armadas X/Y" (env = settings.TRADERSPOST_ENABLED; global = GlobalProfile
   traderspost_enabled y not dry_run; armadas = StrategyProfile con
   traderspost_enabled y not dry_run). Solo LECTURA — el armado sigue en
   su flujo con CONFIRMAR.
5. Tests: cada widget con datos y vacío (0 posiciones, sin deliveries, sin
   estudios); el contador de deriva con los 4 estados; los chips con las
   3 capas en combinaciones.

Invariantes: dashboard READ-ONLY (ningún POST nuevo), presupuesto de
queries acotado (una por widget, con límites), partials intactos, solo
paper/demo. "LISTO PARA COMMIT" solo con suite verde.

---

## D. Orden y nota

DASH-1 → DASH-2, cada uno deployable solo (flujo NTDEV → commit/push del
operador → pull/restart). DASH-1 incluye una decisión que Opus debe
verificar con datos reales del server (¿el bridge exporta bars 1h?) antes
de llenar o eliminar la columna — que lo reporte en su resumen. Si tras
DASH-2 sobra espacio visual, candidato a retirar: la tarjeta "Live 0"
(el sistema es solo paper/demo por invariante — mostrará 0 siempre;
puede sustituirla el badge de deriva).
