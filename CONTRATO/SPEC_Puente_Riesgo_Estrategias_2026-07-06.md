# SPEC — Puente Riesgo ↔ Estrategias · 2026-07-06

> Diseño acordado con el operador. Origen: auditoría de coherencia 2026-07-06 —
> 4 estudios validados (ES, RTY, GC, NQ), **cero aplicados**, TP vivo 6×ATR por
> debajo del p99 de cierres (estrangula el edge), NQ con escalera 4×/5×ATR que
> casi nunca llena, y ninguna visibilidad de `backstop_points`/`tp_nominal_*`
> desde la UI ni la API.

## Principios (no negociables)

1. **Sin fusión de pestañas.** Riesgo = laboratorio (recalculable); Estrategias
   = sala de máquinas (despacha). Un recálculo del estudio JAMÁS muta la config
   viva en silencio.
2. **Aplicar es una decisión del operador**: siempre con diff a la vista y
   confirmación explícita. Nunca automático, nunca al validar el estudio.
3. **Una sola identidad**: el `strategy_id` se teclea UNA vez en todo el ciclo
   de vida. Riesgo crea identidades nuevas (embudo); Estrategias las promueve o
   adopta. El mapeo id↔clave sigue siendo `clave_de()` (routes_riesgo.py).
4. **Aplicar no toca el kill-switch**: `mode`, `dry_run`, `traderspost_enabled`
   y `status` quedan intactos. Solo palancas de riesgo.
5. Invariantes de siempre: fail-closed, guarda P0, `symbol_busy`, solo
   paper/demo.

## Pieza 1 — Visibilidad y deriva (primero: es solo-lectura, riesgo cero)

**a) API.** `/api/strategies/{id}/config` (routes_api.py:199) expone hoy solo
sl/tp ×ATR y scale_entry. Agregar a `override` y `effective`:
`backstop_points`, `tp_nominal_long`, `tp_nominal_short`, `short_size_factor`,
`entry_reserve_timeout_seconds`.

**b) Pestaña Estrategias** (strategy_detail.html, sección "SL / TP POR ATR"):
- Si `backstop_points` está en el pcfg: aviso ámbar
  "⚠ SL×ATR **ignorado** — stop de precio fijo activo: `<pts>` pts (Motor de
  Riesgo)". Igual para TP: "TP×ATR ignorado — TP nominal L `<x>`× / S `<y>`×".
- Mostrar los valores MR (solo lectura) + `short_size_factor` si existe.

**c) Badge de deriva** (helper puro `deriva_estudio(pcfg, activacion) -> str`
en routes_riesgo.py, comparando el pcfg vivo contra `_activacion_json(reco)`
campo a campo):
- `aplicada` (verde) · `difiere del estudio <fecha>` (ámbar) · `sin aplicar`
  (ámbar) · `sin estrategia viva` (gris).
- Se muestra en la tarjeta "Configuración a aplicar" (riesgo.html) y junto a la
  sección SL/TP de strategy_detail.

**Tests:** API expone los 5 campos; el aviso "ignorado" aparece solo con
backstop presente; deriva en los 4 estados.

## Pieza 2 — Aplicar recomendación con diff supervisado

**a) Preview.** `GET /ui/riesgo/aplicar/preview?strategy=<id>` → JSON con
filas de diff: `campo · vivo · recomendado · efecto` usando
`_activacion_json(reco)` vs pcfg actual. Incluye SIEMPRE dos filas
informativas: (1) "sl_atr_multiplier quedará ignorado (backstop manda)";
(2) "⚠ cancel_after `<s>`s: fijar A MANO el mismo valor en TradersPost".
Errores: 400 sin estrategia viva o sin recomendación validada.

**b) Aplicar.** `POST /ui/riesgo/aplicar` body `{strategy}`:
- Merge de `_activacion_json(reco)` sobre `StrategyProfile.pipeline_config_json`
  (patrón existente: leer cfg → mutar → `profile.pipeline_config_json = cfg`,
  cuidado con re-asignar para que SQLAlchemy detecte el cambio, como en
  routes_strategies.py:300).
- `scale_entry`: PRESERVAR el `mode` vigente si ya existía (regla NX-11,
  routes_strategies.py:957) — aplicar solo levels/quantities/max.
- AuditLog vía `AuditService.log_strategy_change` (actor="riesgo_aplicar",
  detalle = diff aplicado + fecha del estudio).
- Respuesta con el resumen aplicado; NO toca mode/dry_run/traderspost/status.

**c) UI.** En la tarjeta "Configuración a aplicar" (riesgo.html): botón
"Aplicar a la config viva…" → modal Alpine con el diff del preview →
"Confirmar" → POST → recarga con badge `aplicada`. Deshabilitado si
`sin estrategia viva` (en su lugar, CTA de la Pieza 3).

**Tests:** preview correcto (diff + avisos); aplicar escribe exactamente los
campos del JSON y nada más; kill-switch intacto; NX-11 preservado; audit row;
400s; tras aplicar la deriva es `aplicada`.

## Pieza 3 — Promoción estudio → viva

En la ficha del estudio **validado** cuya id NO tiene estrategia viva
(`link_vivo is None`): botón "➕ dar de alta en Estrategias →" que abre
`/ui/strategies/new?from_estudio=<id>` con prefill: `strategy_id` (bloqueado),
nombre sugerido, timeframe del id, y el activo sugerido desde el instrumento
del estudio (dropdown de symbol_maps del micro correspondiente — p. ej.
estudio ES → sugerir MES; el operador confirma). El webhook de TradersPost lo
pega el operador (no es derivable). El alta nace `paper` + `dry_run` +
`traderspost_enabled=False`.

Tras crear, redirigir a Riesgo con el modal de la Pieza 2 abierto (aplicar en
el mismo flujo). La dirección inversa (viva adopta estudio) ya existe: dropdown
"estrategia viva (sin estudio)" del panel de upload — no se toca.

**Tests:** prefill correcto; alta nace desarmada; botón solo aparece con
estudio validado y sin viva; flujo completo alta→aplicar.

## Orden y alcance

Pieza 1 → 2 → 3 (cada una deployable sola). Si aprieta el tiempo, lo intocable
es **1 + 2** (visibilidad + aplicar); la 3 es alto valor pero recortable.

Fuera de alcance: aplicar automático, mover el estudio a la DB, tocar el
motor (`scripts/mr_*`), sizing/equity, y cualquier cosa live/real.

## Nota operativa pendiente (independiente del lote)

Las 4 recomendaciones validadas siguen SIN aplicar. Con la Pieza 2 se aplican
en 4 clics; si se necesita antes, el JSON de activación de cada estudio ya
está en la tarjeta "Configuración a aplicar" (copiar → pipeline_config_json).
Recordar `cancel_after` a mano en TradersPost (ES: 2760s; RTY/NQ: 3600s).
Además: ES_ConfStrong y 6J viven sin TP (sin bracket completo) — decidir
estudio o TP nominal manual antes de la activación demo.
