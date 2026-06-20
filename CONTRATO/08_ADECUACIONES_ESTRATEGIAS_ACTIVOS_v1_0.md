# NTEXECG — Adecuaciones: Estrategias, Activos y Riesgo v1.1
### Anexo al Contrato Técnico (complementa los documentos 00–07)

**Fecha:** 2026-06-19 · **Estado:** para incorporación al contrato
**Origen:** consolida `NTEXECG_diseno_continuacion_con_anexo.md` (diseño + Anexo A/B) tras el
análisis `REPORTES/ANALISIS_DISENO_CONTINUACION_2026-06-19.md`.

---

## 0. Naturaleza de este documento

Este anexo **complementa, no reemplaza** a los documentos 00–07. Precisa el detalle de
**estrategias, activos y riesgo** que faltaba aterrizar: guardarraíles por estrategia, perfil de
cuenta, catálogo de instrumentos, ventanas repetibles y la ficha de registro.

Regla de lectura: si algo aquí choca con una **regla no negociable** del doc 00 §14, **manda el
doc 00** (ver §7 de este anexo, "No adoptado"). Todo lo demás se integra a los 5 niveles y al
modelo de datos existentes; **no** se renumera el pipeline ni se cambia el endpoint en producción.

---

## 0-bis. Revisión v1.1 (2026-06-19) — corrección de alcance de riesgo

> Esta revisión **supersede** lo que v1.0 decía sobre riesgo monetario. Decisión del
> operador, actuando como autoridad del contrato.

**Principio:** la **única** responsabilidad de riesgo de NTEXECG es **agregar el Stop Loss
obligatorio por ATR** (doc 00 §8 Nivel 5). NTEXECG **no**:

- controla la **cantidad de contratos** — viene de LuxAlgo en el payload; NTEXECG no la fija
  ni la limita;
- impone **límites monetarios por operación** (no hay gate de `quantity × stop × tick`);
- administra el **riesgo de la cuenta** por pérdidas/ganancias — **no recibe fills ni P&L de
  ningún broker** (Position State es estimado), así que no puede ni debe hacerlo.

**Quedan FUERA del sistema** (eliminados del código y de este anexo): los checks
`qty_exceeds_max`, `risk_exceeds_max`, `stop_required` y `account_daily_risk_reached`, y la
capa de **riesgo diario por cuenta**.

**Lo que SÍ permanece** de este anexo: los **guardarraíles de integridad**
(`symbol_mismatch`, `interval_mismatch`, `signal_stale`), el **catálogo de instrumentos** (§4)
—ahora como **dato de referencia** para la ficha de UI, no para ningún gate de riesgo— y el
**SL por ATR** del contrato.

**Decisiones actualizadas (supersede §8):**
- **D1** → el SL por ATR es el único mecanismo de riesgo monetario. *(Sin gate de riesgo $.)*
- **D3** → el riesgo diario por cuenta se **delega al broker/prop**. NTEXECG no lo administra.

> Las secciones §2 (filas de cantidad/riesgo $/stop/cuenta), §3 (perfil de cuenta como dueño
> del riesgo), §7 (gate de riesgo $) y §8 (D1/D3) se leen a la luz de esta revisión.

---

## 1. Alcance (qué sí y qué no toca NTEXECG)

**SÍ (NTEXECG):** ventana de operación por estrategia, rechazo por antigüedad de señal, validación
de símbolo/contrato y temporalidad, dedup, límites de riesgo por operación y por cuenta, SL
obligatorio, cierre forzado EOD.

**NO (queda en TradersPost / broker):** tipo de orden, TIF, price type, cancelaciones/retries de
ejecución, manejo de fills. TradersPost es un **ejecutor "tonto"**: recibe algo que NTEXECG ya
declaró válido.

> **Regla de oro de las ventanas:** una sola fuente de verdad para la decisión fina = **NTEXECG**.
> TradersPost conserva solo un *backstop amplio* (sesión completa) con "exits/cancels outside
> windows" ON, para que un fallo nuestro nunca dispare a las 3am. Nunca ventanas finas en ambos
> lados.

---

## 2. Nuevos checks del pipeline (se insertan en los 5 niveles existentes)

Estos checks **no existen aún en el código** y se incorporan al `FilterPipeline`. Se mapean a los
niveles del doc 00 §8; el orden fail-fast se respeta.

| Check nuevo | Regla | Nivel destino | Código de rechazo |
|---|---|---|---|
| **Símbolo esperado** | `payload.ticker == strategy.simbolo_esperado` | N1 (sistema) | `symbol_mismatch` |
| **Temporalidad esperada** | `int(payload.interval) == strategy.temporalidad_esperada` | N1 (sistema) | `interval_mismatch` |
| **Antigüedad de señal** | `now − payload.time ≤ umbral` (entrada/salida) | N2 (temporal) | `signal_stale` |
| **Cantidad máxima** | `payload.quantity ≤ strategy.cantidad_maxima_contratos` | N3 (riesgo) | `qty_exceeds_max` |
| **Riesgo $ por operación** | `quantity × stop_ticks × valor_tick ≤ riesgo_usd_max_operacion` | N3 (riesgo) | `risk_exceeds_max` |
| **Stop obligatorio** | si `stop_obligatorio` y no hay stop → rechazo | N3/N5 | `stop_required` |
| **Riesgo diario por cuenta** | agregado del día de la cuenta ≤ `riesgo_usd_max_diario` | N3 (riesgo) | `account_daily_risk_reached` |

Notas:
- `símbolo esperado` y `temporalidad esperada` son **guardarraíles por estrategia** (atrapan chart
  equivocado ES/MES, 6J/M6J, y timeframe incorrecto). Son *adicionales* al Symbol Mapper, que
  sigue traduciendo el ticker al contrato vigente.
- `valor_tick` y `tipo_contrato` se resuelven **del catálogo de instrumentos** (§4), nunca del
  payload ni de una copia en el perfil (evita riesgo de dato desactualizado).
- Las **salidas** siguen exentas de N3–N5 (prioridad absoluta, doc 00 §14.7).

---

## 3. Nueva entidad — Perfil de cuenta

El riesgo diario **no es de la estrategia**, es de la **cuenta** (agrega el riesgo de todas las
estrategias que apuntan a esa cuenta). Hoy el contrato lo ubica en `global_profile`; se separa.

**Tabla sugerida: `account_profiles`**

| Campo | Tipo | Origen | Notas |
|---|---|---|---|
| id | uuid | 🔒 | |
| nombre | texto | ✍️ | p. ej. `PAPER_FUTURES`, `NT_Principal` |
| broker | texto | ✍️ | paper / Tradovate / Apex / IBKR |
| modo | enum(`paper`,`live`) | ✍️ | separación paper/live explícita (doc 00 §14.12) |
| riesgo_usd_max_diario | moneda | ✍️ ✅ | tope agregado del día por cuenta |
| trailing_drawdown_usd | moneda | ✍️ | regla de prop si aplica |
| activa | bool | ✍️ | |

> **Decisión D3 (ver §8):** el dueño del riesgo diario por cuenta es NTEXECG (capa de cuenta).
> Si se delegara al broker/prop, debe documentarse explícitamente y el check
> `account_daily_risk_reached` quedaría informativo.

---

## 4. Nueva entidad — Catálogo de instrumentos

Propiedades fijas del contrato. La estrategia **referencia el símbolo**; NTEXECG autocompleta tipo,
`valor_tick` y `tick_minimo`. **Elimina de raíz el error de tecleo del valor de tick.**

**Tabla sugerida: `instrument_catalog`** (o extender `symbol_maps` con estas columnas)

| Símbolo | Tipo | Valor/tick | Tick mín. | Nota |
|---|---|---|---|---|
| MYM | micro | $0.50 | 1.00 | Micro Dow |
| M2K | micro | $0.50 | 0.10 | Micro Russell |
| MNQ | micro | $0.50 | 0.25 | Micro Nasdaq |
| MGC | micro | $1.00 | 0.10 | Micro Gold |
| MCL | micro | $1.00 | 0.01 | Micro Crude |
| MNG | micro | $1.00 | 0.001 | Micro NatGas (volátil) |
| MES | micro | $1.25 | 0.25 | Micro S&P |
| M6E | micro | $1.25 | 0.0001 | Micro Euro FX |
| MJY (M6J) | micro | $1.25 | 0.000001 | Micro Yen — **MJY**, no M6J |
| YM | mini | $5.00 | 1.00 | Dow |
| RTY | mini | $5.00 | 0.10 | Russell |
| NQ | mini | $5.00 | 0.25 | Nasdaq |
| 6E | estándar | $6.25 | 0.00005 | Euro FX |
| 6J | estándar | $6.25 | 0.0000005 | Yen |
| ES | mini | $12.50 | 0.25 | S&P |

Fórmula de riesgo: `riesgo_usd = cantidad × stop_ticks × valor_tick`.
Topes por defecto sugeridos: micro → 2 contratos, mini → 1 (el `riesgo_usd` manda sobre el conteo).

---

## 5. Ventanas de operación — grupo repetible

Corrección estructural sobre el `asset_profile` actual (un solo `session_config_json`). Cada
estrategia tiene **N ventanas**, y **cada ventana lleva sus propios días**. Resuelve casos como
"L–J 09:00–15:45 / V 09:00–12:00".

**Por ventana:** `dias` (multiselect L,M,X,J,V,S,D) · `hora_inicio` · `hora_fin`.
**A nivel estrategia:** `timezone` (default America/New_York) · `salidas_siempre_permitidas`
(default true) · `cierre_forzado_eod` (hora; default 16:00).

> Compatibilidad: el `session_config_json` actual sigue siendo válido como caso de **una sola
> ventana**; la evolución es aditiva.

---

## 6. Ficha de registro de estrategia (especificación de formulario — doc 02)

Leyenda de **Origen**: 🔒 sistema · ✍️ usuario · ✅ guardarraíl de validación · 📚 autocompletado
desde catálogo.

**6.1 Identidad:** id_interno 🔒 · nombre_corto ✍️ *(debe iniciar con el símbolo operado, p. ej.
`MES15m…`)* · descripcion ✍️ *(documentar fuente y factor de escalado, §9d)* · url_unica 🔒 ·
token 🔒 *(secreto, 32 bytes)* · estado 🔒/✍️ *(default `disabled`)* · fecha_alta 🔒 · responsable
✍️ · created_at/updated_at/version 🔒.

**6.2 Definición LuxAlgo + backtest:** toolkit/categoría · gatillo · filtro_1/filtro_2 ·
condicion_salida · backtest_inicio/fin · num_operaciones · winrate · profit_factor ·
max_drawdown_pct · beneficio_neto · avg_win/avg_loss · max_drawdown_usd · frecuencia ·
tamaño_orden_backtest. *(Todos ✍️.)*

**6.3 Instrumento y temporalidad (guardarraíles):** simbolo_esperado ✍️✅ *(valida vs `ticker`)* ·
tipo_contrato 📚 · valor_tick 📚 · tick_minimo 📚 · temporalidad_esperada ✍️✅ *(valida vs
`interval`)*.

**6.4 Ventanas:** grupo repetible (§5).

**6.5 Filtros de calidad:** antiguedad_entrada_seg ✍️✅ · antiguedad_salida_seg ✍️✅ · dedup_seg
✍️✅ · confirmaciones_adicionales ✍️ *(futuro)*.

**6.6 Riesgo (per-estrategia):** stop_obligatorio ✍️✅ · stop_esperado_ticks ✍️ ·
riesgo_usd_max_operacion ✍️✅ · cantidad_maxima_contratos ✍️✅.
*(El riesgo diario NO va aquí → perfil de cuenta, §3.)*

**6.7 Ruteo de salida:** webhook_traderspost ✍️ · cuenta_objetivo ✍️ *(ref. a perfil de cuenta)* ·
notas_ruteo ✍️.

---

## 7. Modelo de riesgo y SL — reconciliación (punto crítico)

El doc 00 exige **SL dinámico por ATR** como protección principal (regla no negociable #6). El
documento de continuación usaba un **stop fijo en ticks**. Se reconcilian así:

```text
SL principal  = por ATR (entry ± ATR × sl_atr_multiplier)   ← se conserva (doc 00 §8 N5)
Gate adicional = riesgo $ ≤ máx  (qty × stop_ticks × valor_tick)  ← se agrega (este anexo §2)
stop_esperado_ticks = fallback cuando el ATR no esté disponible   ← decisión D1 (§8)
```

- El SL por ATR **no se reemplaza**: requiere datos de mercado (bridge NinjaTrader) y es la mayor
  protección del sistema.
- La validación de **riesgo en dólares** es complementaria: limita la exposición por operación
  usando el `valor_tick` del catálogo.
- La dependencia del bridge para **entradas** se mantiene: "si NT inactivo → BLOCK entradas, PERMIT
  salidas" (doc 00 §14.25).

---

## 7-bis. NO adoptado del documento de continuación (con razón)

Para que el malentendido no se repita, se rechazan explícitamente estas simplificaciones:

1. **Stop fijo en ticks en lugar de ATR** → rechazado como mecanismo principal; el ATR es regla de
   contrato. (El stop en ticks queda solo como fallback — D1.)
2. **NTEXECG sin datos de mercado** (sin ATR, sin heartbeat, sin scoring) → rechazado; rompería la
   protección de SL y el check de bridge activo.
3. **Cambiar el endpoint a `/hook/{token}`** → rechazado; se mantiene
   `/webhooks/luxalgo/{strategy_id}?token=` (ya en producción con HTTPS). Es equivalente conceptual.
4. **Reenviar el payload sin traducir el símbolo** → por defecto se mantiene el Symbol Mapper
   (envía el contrato vigente a TradersPost). Sujeto a D2.

---

## 8. Decisiones — CONFIRMADAS por el operador (2026-06-19)

Las cuatro decisiones quedaron **confirmadas**; el sistema se construye con la columna "Resolución".

| ID | Decisión | Resolución confirmada | Alternativa descartada |
|---|---|---|---|
| D1 | Mecanismo de SL | ✅ **ATR principal + riesgo $ como gate; ticks fijos solo fallback** | Stop fijo en ticks |
| D2 | Símbolo a TradersPost | ✅ **Symbol Mapper (envía contrato mapeado)** | Reenviar payload original (Allowed tickers de TradersPost) |
| D3 | Dueño del riesgo diario | ✅ **NTEXECG (capa de cuenta)** | Delegar a broker/prop |
| D4 | Umbrales de antigüedad | ✅ **Por estrategia, separados entrada/salida (seg)** | Umbral global |

> Estas resoluciones son firmes. Cualquier cambio futuro requiere nueva versión del anexo (v1.1+).

---

## 9. Hallazgos operativos (a manual de operación / DOCS)

**9a. Correcciones de configuración en TradersPost** (se corrigen en TradersPost, no en docs):
ventana de trading 1 terminaba 11:30 **p.m.** → debe ser **a.m.**; exit breakeven offset 1.5 → **0**;
reject-if-signal-older 1 → **2** (5m); Allowed tickers Any → **Only selected** con el símbolo micro.

**9b. Micro vs estándar:** nunca "quitar la M". Escribir el **símbolo micro explícito** (MES, MJY).
El catálogo (§4) elimina el error de valor de tick: no se teclea, se referencia. Verificación
obligatoria en paper: confirmar que el contrato abierto es el micro de su valor de tick esperado.

**9c. Choques de TradersPost:** la config vive en la **suscripción**, no en la estrategia (cada
suscripción arranca en defaults); routing/fills duplicados (mantener una alerta → un webhook → una
suscripción → un broker mientras se valida); `PAPER_STOCKS` no recibe futuros; interlock
ticker ↔ Allowed tickers debe coincidir.

**9d. Escalado de métricas ES → MES** (LuxAlgo no tiene estrategias de micros; backtest en el
estándar): las métricas **en dólares se dividen entre 10** (neto, drawdown $, avg win/loss); las
**porcentuales NO cambian** (winrate, PF, drawdown %). Documentar el factor por estrategia.

---

## 10. Impacto en el roadmap (dónde encaja cada adopción)

| Adopción | Fase sugerida | Artefacto a actualizar |
|---|---|---|
| Checks símbolo/interval/antigüedad | Fase 2 | `filter_pipeline.py`, doc 01 REQ-05xx |
| Cantidad máx + riesgo $ por operación | Fase 2 | `filter_pipeline.py`, doc 04 |
| Perfil de cuenta + riesgo diario | Fase 3 | nuevo modelo `account_profiles`, doc 04 |
| Catálogo de instrumentos (tick values) | Fase 2 | `symbol_maps`/nueva tabla, seed, doc 04 |
| Ventanas repetibles | Fase 3/4 | `asset_profile`, UI doc 02 |
| Ficha de registro detallada | Fase 1/2 (UI) | formulario UI, doc 02 |
| Hallazgos operativos §9 | inmediato | DOCS / manual de operación |

---

## 11. Criterios de aceptación del anexo (para validar en paper)

```text
✓ Señal con ticker ≠ simbolo_esperado → rechazada (symbol_mismatch)
✓ Señal con interval ≠ temporalidad_esperada → rechazada (interval_mismatch)
✓ Señal más vieja que el umbral → rechazada (signal_stale)
✓ quantity > cantidad_maxima_contratos → rechazada (qty_exceeds_max)
✓ qty × stop_ticks × valor_tick > riesgo_usd_max_operacion → rechazada (risk_exceeds_max)
✓ stop_obligatorio sin stop → rechazada (stop_required)
✓ Riesgo diario de la cuenta excedido → entradas bloqueadas (account_daily_risk_reached)
✓ Entrada APPROVE sigue llevando SL por ATR (regla doc 00 §14.6 intacta)
✓ Salidas siguen exentas de los gates de riesgo
✓ valor_tick siempre proviene del catálogo, nunca del payload
```
