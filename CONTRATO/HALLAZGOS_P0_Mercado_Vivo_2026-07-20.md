# HALLAZGOS P0 — primera prueba con MERCADO VIVO · 2026-07-20

> Prueba T2/T3 sintética sobre ES5m_ConfNormal (paper, mercado abierto).
> DOS P0 que ninguna auditoría de código podía encontrar: solo aparecen
> cuando el broker responde. Ambos MITIGADOS (no resueltos) el mismo día.

## P0-1 — EL EXIT NO CIERRA LA POSICIÓN COMPLETA (el más grave)

**Evidencia:** entrada escalonada creó posición de **5 micros** (C1 del
reparto [5,3,2]); el exit viajó con `"quantity": 1` (la cantidad de la
ALERTA de LuxAlgo) y TradersPost cerró **exactamente 1 micro**. Quedaron
**4 vivos** en el broker mientras NTEXECG marcaba la posición cerrada.
Señal de entrada `baffdba5-…` (14:41), exit `7ffc9022-…` (15:40).

**Mecánica:** `payload_builder.build` usa `signal.quantity` también en los
exits, mientras la entrada la dimensiona `scale_entry.quantities` (micros
absolutos). Entrada 5 ↔ salida 1. Afecta a TODOS los caminos de salida
(exit LuxAlgo, forced_exit EOD/max_holding, reversal). Con la escalera
funcionando serían 10 abiertos y 1 cerrado.

**Impacto:** divergencia de estado NTEXECG↔broker, exposición no
gestionada, contaminación de ExecutionResult (fills de huérfanos sin
decisión asociada), y `symbol_busy`/reversals operando sobre una posición
estimada falsa.

**MITIGACIÓN aplicada (no es el fix):** `set_scale_execution --all --off
--apply` → todas en `design_only`: entrada = cantidad de la alerta =
cantidad del exit ⇒ cierre completo. Backup en
`REPORTES/scale_exec_backup_20260720_155042.json`.

**FIX pendiente (lote P0-EXIT-PARCIAL, prompt en el chat del arquitecto):**
los exits deben cerrar la posición COMPLETA — omitiendo `quantity` si
TradersPost aplana con eso (verificar en su spec), o enviando la cantidad
realmente abierta (`PositionState.quantity`, ya trackeada). Incluye barrido
forense de posiciones huérfanas dejadas desde que `scale_entry` pasó a
`execute`.

## P0-2 — LA ESCALERA NUNCA LLEGÓ AL BROKER

**Evidencia:** las tres piernas se despacharon correctamente (WebhookDelivery
SENT, HTTP 200, `success:true`, `cancelAfter: 3600`, precios al tick:
C2 3@7504.75, C3 2@7493.25) pero en TradersPost **solo existe C1 con su
bracket** (orden padre 585947370051, TP 7576.50 y SL 7426.50, ambos qty 5).
Las compras límite nunca se convirtieron en órdenes.

**Consecuencia:** la compra escalonada — el corazón del diseño Luxy — nunca
ha operado en producción. Todo el andamiaje de fills de C2/C3, curvas de
llegada, participación y el re-armado completo (RA-0..RA-3) descansa sobre
piernas que el broker jamás creó.

**Hipótesis a discriminar (no confirmada):** (a) TradersPost ignora un
segundo `action:"buy"` con posición abierta — su acción documentada para
sumar es `"add"` (el propio comentario de `payload_builder` dice "# add:
requiere precio base…", la intención original); (b) guarda de señal
duplicada: C1/C2/C3 salieron en el MISMO segundo (14:41:29.5) con mismo
símbolo y acción. Falta la vista de procesamiento de TradersPost
(Signals/Activity), no el webhook log (que solo acusa recibo).

**MITIGACIÓN:** la misma — `design_only` (sin escalera no hay piernas
fantasma). **FIX pendiente:** lote ESCALERA-NO-LLEGA (investigación primero,
sin tocar código hasta confirmar contra la cuenta real).

## Hallazgo colateral (cerrado el mismo día)

**FILTROS-OFF nunca se había aplicado en producción**: el payload en vivo
delató `"filters_active": true, "ntexecg_score": 58` en ES (mínimo 55 — pasó
por 3 puntos). El lote estaba desplegado pero la FASE 2 (`--apply`) no se
había corrido. Corregido: `inventario_l4 --all --apply` → **7/7 passthrough**
(retirados `filters`+`score_minimum` de ES-CN, GC, ES-CS y `regime` de RTY).
Backup en `REPORTES/filtros_l4_off_backup_20260720_155051.json`.

## Estado al cierre del día

- 7/7 sin filtros N4 · 7/7 en `design_only` (entrada=salida, sin huérfanos
  nuevos) · re-armado APAGADO en todas.
- **Tarea manual pendiente del operador:** aplanar a mano los micros
  huérfanos en AMBAS cuentas (base y APEXsim) — los 4 de esta prueba y
  cualquier resto de días previos con `execute`.
- **Orden de los lotes:** P0-EXIT-PARCIAL → ESCALERA-NO-LLEGA → (verificar
  ambos en vivo) → re-activar `execute` → P1s de la auditoría final
  (E3b flujo principal, A-3, A-5, B-2, B-4) → re-armado en RTY.

## Lección para el registro

Dos auditorías de código, un fixture de oro con valores a lápiz y 1455 tests
verdes no vieron ninguno de estos dos P0 — porque ninguno es un error de
código: son supuestos sobre el comportamiento del BROKER que nadie había
verificado. **Ninguna cadena de despacho puede declararse correcta sin
haberla visto ejecutar contra el broker real, una vez, con los ojos.**
