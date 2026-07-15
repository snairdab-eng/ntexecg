# SPEC — Re-armado supervisado de piernas escalonadas (C2/C3) · 2026-07-15

> Arquitecto: Fable 5 (sesión de cierre). Problema del operador: TradersPost
> cancela las órdenes límite a los ≤60 min (configurable 1–60), y las piernas
> profundas (C2/C3) muchas veces necesitan más — el propio estudio de fills lo
> midió: a 2.0×ATR la mediana de llegada es 50 min y el p90 152 min. Hoy esas
> piernas mueren antes de que el precio llegue.

## 0. Alternativas evaluadas (y por qué re-armado)

- (a) **Armar la pierna solo cuando el precio se acerque** (NTEXECG vigila el
  feed del bridge): REACTIVO PERO FRÁGIL — el feed es de ~10s y las piernas
  profundas se llenan justo en los picos de velocidad del mercado; llegar tarde
  = fill perdido que el estudio SÍ contó. Descartada como mecanismo primario.
- (b) **Re-armado por ciclo** (elegida): las piernas se mandan desde el inicio
  (como hoy) con cancel_after ≤60min en TradersPost, y NTEXECG las RE-ENVÍA al
  expirar el ciclo mientras la posición madre siga abierta. La orden descansa en
  el book todo el tiempo (mismo modelo del estudio), el límite de 60 min de
  TradersPost se vuelve un detalle de implementación, y el ciclo de vida queda
  acotado por la POSICIÓN, no por el reloj de TradersPost.

## 1. Diseño

1. **Ciclo SIN SOLAPE** (hallazgo del operador 2026-07-15): TTL de pierna en
   TradersPost = 60 min, y el re-envío ocurre al minuto 61–62 — DESPUÉS de que
   el cancel_after ejecutó con certeza, jamás antes. Dos órdenes límite al mismo
   precio coexistiendo = riesgo de fill doble (posición 2×) — inaceptable.
   El costo es una ventana ciega de ~1–2 min por ciclo (~2–3% del tiempo):
   preferible perder un fill ocasional que duplicar tamaño jamás (asimetría de
   la misión). Antes de cada re-envío, el job verifica que la pierna NO se
   llenó en el ciclo anterior; estado ilegible → no re-arma (fail-closed, §3).
   Un job (junto a ExitManagerJob) revisa cada minuto las posiciones abiertas
   con piernas pendientes y re-envía la MISMA orden límite (mismo precio, misma
   cantidad, nuevo client_order_id correlacionado: `<base>-r2`, `-r3`, …).
   MEJORA FUTURA (si la auditoría E2E confirma que TradersPost acepta cancelar
   por webhook de forma confiable): cancelar-confirmar-reemplazar, que cierra
   la ventana ciega a segundos. Optimización, no requisito.
2. **Fin del ciclo de vida** (lo que hoy queda ambiguo): al CERRARSE la posición
   madre (exit de LuxAlgo, backstop, TP) → STOP de re-armado inmediato + se deja
   morir el cancel_after del último ciclo (o cancelación explícita si el flujo
   de exit lo permite — decidir en implementación con lo que TradersPost
   soporte). INVARIANTE: jamás una pierna viva sin posición madre.
3. **Fail-closed**: si el estado de la posición no es legible (PositionState
   caído, bridge sin heartbeat) → NO re-armar (una orden que descansa sin
   vigilancia es riesgo, no participación). El re-armado es best-effort; el
   corte de TradersPost sigue siendo la red de seguridad dura.
4. **Cotas**: MAX_REARMADOS_POR_PIERNA (p. ej. 12 ≈ 11 h) y respeto del
   max_micro_contracts del perfil en cada re-envío (si el perfil cambió a media
   vida, el re-envío recalcula tamaño con el catálogo vigente).
5. **AuditLog**: cada re-armado deja huella (pierna, ciclo n, precio, qty) — la
   observación en demo debe poder reconstruir cuántos fills vinieron de ciclos
   tardíos.

## 2. Honestidad del modelo (motor)

El estudio hoy evalúa fills "con corte 3600s" (conservador) y "sin corte"
(techo). El re-armado mueve la realidad hacia un punto intermedio bien definido:
**pierna viva mientras viva la posición**. El motor gana ese tercer modo:
`leg_filled` acotado por la DURACIÓN del trade (dato que ya existe) en vez del
reloj fijo — `mr_sims`/`mr_luxy` lo exponen como el modo por default cuando el
re-armado esté ON, y la Tabla A/B/perfiles se derivan con él. R-T1 se extiende:
"fills con corte = política de despacho vigente". NADA de esto se aplica a
producción sin pasar por el estudio → gate → Aplicar (flujo de siempre).

## 3. Orden de implementación sugerido (lotes para Opus)

1. **RA-1 (motor)**: tercer modo de fills en mr_sims/mr_luxy (corte por duración
   del trade) + columnas comparativas en el estudio (con corte 1h · re-armado ·
   sin corte). Solo estudio, cero despacho. Revisión estándar.
2. **RA-2 (despacho)**: el job de re-armado + invariantes 1–5 + tests
   adversariales (posición cerrada → no re-arma; estado ilegible → no re-arma;
   cotas; correlación de client_order_id; AuditLog). REVISIÓN PROFUNDA
   (toca despacho real).
3. **RA-3 (UI)**: toggle por estrategia en Config (default OFF hasta observar
   en demo), visibilidad de ciclos en Posiciones.

## 4. Interacción con la auditoría E2E del 2026-07-15

La auditoría documenta el ciclo de vida ACTUAL de las piernas (qué queda
huérfano al cerrar). Sus hallazgos alimentan RA-2 — no implementar RA-2 antes
de leer esa auditoría.
