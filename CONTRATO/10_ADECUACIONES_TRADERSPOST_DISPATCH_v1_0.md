# NTEXECG — Adecuaciones: Integración de Despacho con TradersPost v1.0
### Anexo 10 al Contrato Técnico (complementa los documentos 00–09)

**Fecha:** 2026-06-24 · **Estado:** para incorporación al contrato
**Origen:** primera prueba de **envío real a TradersPost paper** (Etapa C). Varios requisitos
de formato y de broker **solo se manifiestan enviando real** (no en DRY_RUN); aquí se documentan
y se registra la decisión de activar Take Profit por ATR.

---

## 0. Naturaleza de este documento

Complementa, **no reemplaza**, a los documentos 00–09. Documenta los requisitos del payload de
TradersPost descubiertos en la prueba real y **ajusta** el principio de "solo SL" del Anexo 08
§0-bis para los brokers que exigen orden bracket.

---

## 1. Resultado de la prueba (Etapa C) — primer round-trip real a paper

- Estrategia `ES5m`, instrumento `MESU2026`, 1 contrato, cuenta **paper** de TradersPost.
- Pipeline completo validado en vivo: webhook → 5 niveles → **APPROVE** + SL/TP por ATR →
  dispatch (compuerta Fase 2) → TradersPost → **broker FILLED**.
- Entrada: **filled @ 5610.75** (bracket TP+SL). Salida: **filled @ 5612.25** (sell). Flat.
- P&L bruto ≈ **+$7.50** (1.5 puntos × $5/punto en MES), antes de comisiones.
- Tras la prueba se **desarmaron** el despacho global y el de la estrategia (vuelta a DRY_RUN).

---

## 2. Requisitos de formato del payload de TradersPost (3 correcciones)

Descubiertos al enviar real; corregidos en `PayloadBuilder` y desplegados:

1. **Stop loss absoluto → clave `stopPrice`** (no `price`):
   `"stopLoss": {"type": "stop", "stopPrice": <precio>}`.
   Sin esto: HTTP 400 `invalid-stop-loss-value-required`.
2. **Take profit absoluto → clave `limitPrice`**:
   `"takeProfit": {"type": "limit", "limitPrice": <precio>}`.
3. **`sentiment` solo en entradas (buy/sell)**: en `action=exit` **no** debe enviarse
   `sentiment` → HTTP 400 `invalid-sentiment-action`. NTEXECG ahora lo **omite en salidas**.
   (El webhook LuxAlgo→NTEXECG sigue mandando `sentiment=flat`; NTEXECG lo normaliza a `exit`
   y no lo reenvía a TradersPost.)

---

## 3. Requisito de bracket (TP+SL) — decisión: activar Take Profit por ATR

- El broker conectado a TradersPost **no soporta órdenes OTO** (entrada con **solo** stop loss):
  exige **bracket completo (TP + SL juntos)** o ninguno. Error: `oto-orders-not-supported`
  ("You are required to send both a take profit and stop loss").
- **Decisión D-10.1:** NTEXECG **activa un Take Profit por ATR** para enviar bracket completo,
  **manteniendo el SL obligatorio**. Esto **ajusta** el principio del Anexo 08 §0-bis / contrato
  (donde el TP lo manejaba LuxAlgo): **cuando el broker exige bracket, NTEXECG añade el TP**.
- **TP por defecto: ATR × 6.0** — amplio a propósito, para que la salida de LuxAlgo dispare
  normalmente antes; el TP cumple el requisito del broker sin gobernar la estrategia. El SL
  sigue en **ATR × 1.5**. Configurable por estrategia/activo vía `tp_atr_multiplier`.
- **Implementación:** el mecanismo ya existía (`SLTPCalculator` calcula el TP; `PayloadBuilder`
  lo incluye); se activó fijando `tp_atr_multiplier` en el `StrategyProfile`. **Pendiente:**
  exponer `tp_atr_multiplier` en la UI (hoy se fija por script/BD).

---

## 4. Reconciliación nativa desde el export de TradersPost (Fase 8)

El export "trades" de TradersPost trae, por fila:
- nuestro **`extras.signal_id`** (dentro de la columna `Signal Payload`),
- el `Status` (failed / submitted), y
- el `Trade Result` con `entryOrder.filledAvgPrice` y `exitOrder.filledAvgPrice` (precios de
  fill reales) y `status` (filled).

**Implicación:** la reconciliación exacta y el **P&L real** se pueden obtener **directamente del
export nativo** (por `signal_id` + precios de fill), sin el llenado manual de la plantilla
`DOCS/resultados_semanales_PLANTILLA.csv`. **Pendiente:** importador nativo del CSV de TradersPost
(parsear `Signal Payload` + `Trade Result`; emparejar entry/exit por símbolo+estrategia para el
P&L). Se mantiene la plantilla manual como alternativa.

---

## 5. Decisiones confirmadas (2026-06-24)

- **D-10.1** Activar **TP por ATR (bracket)** cuando el broker lo exija; default **ATR × 6.0**,
  configurable por estrategia; el SL sigue en **ATR × 1.5**.
- **D-10.2** **Modo seguro por defecto:** tras la prueba se desarmaron global y estrategia
  (DRY_RUN). El kill-switch del servidor (`env TRADERSPOST_ENABLED`) puede quedar en `true` pero
  **sin efecto** mientras los toggles de UI estén en DRY_RUN (semántica de candados en capas).

---

## 6. Pendientes derivados

- Exponer `tp_atr_multiplier` en la UI (formulario de alta + detalle).
- **Importador nativo de TradersPost** para la Fase 8 (reconciliación + P&L automáticos).
- **Conciliar el estado estimado de posición**: tras los intentos fallidos, el estado estimado de
  NTEXECG quedó `LONG` para `ES5m/MESU2026` aunque TradersPost cerró; revisar/limpiar (no afecta
  el envío, pero ensucia la posición estimada).
- ≥5 días en paper sin anomalías antes de avanzar a cuenta real.
