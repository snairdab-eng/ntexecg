# LOTE BUG SL-INSENSIBLE — veredicto: LEGÍTIMO, con hallazgo de presentación

> 2026-07-18 · Parte 1 del lote FIX-D-EJECUCION+SL-INSENSIBLE · Repro:
> ES5m_ConfStrong_TSR_WeakConf, escalera 4/3/3 @ mercado/−$302/−$604,
> SL −$11,322 vs −$6,660 → fila idéntica dígito a dígito.

## Veredicto

**No hay bug de plomería ni caché: la fila es matemáticamente correcta y
DEBE ser idéntica con ambos SL.** El repro reproduce los números del
operador al centavo (net $8,739.26 · peor −$4,524.62 · part 53.3%) llamando
`evaluate_overrides` directo — el motor recibió ambos SL (levers backstop
11322/6660 en cada corrida). Lo que faltaba era que el panel lo DIJERA: el
contador "SL toca 1 de 44" contaba un trade que la fila no ve.

## La aritmética exacta (hipótesis (c), demostrada con traza por-trade)

1. **El único trade que toca cualquiera de los dos SL es el #22 — un CORTO**
   (MAE 237.0 pts = $11,849; el siguiente MAE es el largo #35 con 126.1 pts
   = $6,306). Ambos SL (226.4 y 133.2 pts) caen entre ambos → tocan
   exactamente 1 trade: el #22.
2. **La config vigente corta el lado corto** (palancas derivadas del
   estudio: lado "cortar → solo largos"; el panel arranca con dir=long).
   `eval_levers` excluye los cortos de la fila (participación 53.3% = 24/45)
   → **el #22 no participa** → ningún participante se stoppea → cualquier SL
   por encima de 126.1 pts ($6,306) produce EXACTAMENTE la misma fila. Si el
   #22 participara, la fila sí se movería: su pérdida simulada es −$10,761 @
   226 pts vs −$6,099 @ 133 pts (traza con `luxy_outcome` directo).
3. **El peor −$4,524.62 no es un stoppeado**: es el largo #27 saliendo AL
   CIERRE NATIVO (nativo −$4,662.50, MAE 34.9×ATR — nunca llega a los SL
   probados), con las 3 piernas llenas:
   `0.4·(−93.25) + 0.3·(−93.25+3.06) + 0.3·(−93.25+6.13) pts × $50 =
   −$1,865.00 − $1,352.79 − $1,306.83 = −$4,524.62`. La cercanía con
   0.4·226·50 ≈ $4,529 fue coincidencia numérica.
4. **"SL toca 1 de 44"**: el contador del diagrama contaba TODOS los trades
   con MAE, ignorando dirección y toggles (strategy_detail.html:1248,1267
   pre-fix) — le decía al operador que el SL importaba cuando la fila no
   podía verlo.

Hipótesis descartadas con evidencia: (a) payload — el JS envía `sl_usd` del
slider en cada Recalcular (strategy_detail.html:1530) y el motor lo consumió
(levers.backstop_usd == SL enviado en ambas corridas); (b) caché — cada POST
relanza el job del motor (routes_strategies.py:809-813, `LUXY_EVAL_JOBS`
sobrescrito) y el repro in-process sin caché alguna da la misma fila.

## Lo entregado (mandato del caso legítimo)

1. **`mr_luxy.luxy_desglose`** — núcleo POR PIERNA del desenlace;
   `luxy_outcome` ahora delega en él (fuente única, cero doble aritmética).
2. **`evaluate_overrides` devuelve dos campos nuevos** (solo motor):
   - `peor_desglose`: el peor trade PARTICIPANTE pierna a pierna (número,
     lado, motivo de salida stop/TP/BE/nativo, pnl por pierna, nativo) — el
     peor es PREDECIBLE a mano.
   - `sl_toca`: `{b_pts, tocados, participantes, excluidos_dir,
     excluidos_toggles}` sobre el universo simulable — la fila puede ser
     insensible al SL, pero JAMÁS en silencio.
3. **Panel (strategy_detail.html)**:
   - contador del SL ahora respeta dirección y toggles y declara los
     excluidos: "el SL toca 0 de 24 operaciones · +1 tocada(s) EXCLUIDA(s)
     por dirección/toggles — no mueven la fila";
   - pie VALIDADO bajo la tabla (tras Recalcular): peor trade pierna a
     pierna + toques del SL del motor; Restablecer lo apaga.
4. **Tests (fixture de oro §7)**: contraste dos-SL⇒filas-distintas con
   stoppeado participante (candado contra un futuro bug real de plomería,
   valores a mano 5,831/4,331 y peores −2,150/−3,650); el caso ES_CS
   reproducido (tocado excluido por dir ⇒ filas idénticas Y declaradas);
   desglose == tesela y paridad núcleo↔envoltura (incluido el caso D:
   pierna más profunda que el stop llena con $0.00).

## Repro final (master real ES_CS, campos nuevos)

Ambos SL: `sl_toca = {tocados: 1, participantes: 0, excluidos_dir: 1}` ·
peor = #27 largo, motivo "native", −$4,524.62 = −1,865.00 −1,352.79
−1,306.83 (nativo −$4,662.50). Exactamente lo que el operador vio, ahora
con el porqué en pantalla.

## Nota para el operador

Si quieres que el SL "trabaje" en ES_CS tienes dos caminos: (i) dir=ambos
(el #22 vuelve a la fila y el SL lo capa — la fila se vuelve sensible al
SL), o (ii) con solo-largos, un SL ≤ $6,306 (126 pts, el MAE máximo de los
largos) — por encima de eso el SL respira sobre TODO lo observado del lado
operado y solo protege contra lo no-visto (que es exactamente lo que la
muesca del SL-RESPIRO señala).
