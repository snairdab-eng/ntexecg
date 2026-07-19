# RA-2b SUB-PASO 3 — Inferencia de precio (P3 del diseño)

> 2026-07-19 · Módulo PURO en `app/services/rearm.py` (junto a los sub-pasos
> 1-2) + wrapper no-puro MÍNIMO. Sin scheduler, sin despacho, cero cambio de
> comportamiento. Base: RA2b_RearmJob_Diseno_2026-07-17.md P3.

## Funciones puras

- `tramo_valido(bars, opened_at, timeframe, now, heartbeat_max_age)` →
  barras del tramo validadas o **None** (hueco/ilegible ⇒ fail-closed), con
  los 3 chequeos del diseño: (i) conteo esperado vs real, (ii) high/low
  None o `time` no parseable en el tramo, (iii) barra más nueva más vieja
  que `tf + heartbeat_max_age` (el sello es el INICIO de la barra — una en
  formación lega hasta un intervalo completo; el margen es exactamente un
  intervalo + heartbeat).
- `extremos(tramo)` → (max_high, min_low).
- `nivel_tocado(side, limit, ext)` — long: min_low ≤ limit; short:
  max_high ≥ limit. **Convención INCLUSIVE documentada**: la frontera
  exacta cuenta como tocado — R-RA2 dice "tocado ⇒ jamás re-enviar"; en el
  peor caso se deja de re-armar una pierna que no llenó (lado seguro),
  lo contrario arriesga posición doble.
- `backstop_tocado` / `tp_tocado` (R-RA6, lados invertidos correctos:
  long stop abajo/TP arriba; short al revés; None ⇒ False).
- `atr_expandido(atr_vivo, signal_atr, umbral)` (R-RA7, `>` estricto) —
  datos ausentes/no positivos ⇒ **None** (ilegible ≠ "no expandido"; el
  caller lo trata fail-closed).

Convención de tiempo: barras ET-naive (LX-6; el parseo REUSA
`bar_store.parse_bar_time` — fuente única), `opened_at`/`now` UTC-aware →
normalizados a ET-naive (`_a_et_naive`).

## LA TRAMPA DEL CONTEO — decisión y justificación

El conteo ingenuo "barras esperadas en [opened_at, now]" marcaría como HUECO
los gaps LEGÍTIMOS del calendario CME (mantenimiento 17:00–18:00 ET lun–jue;
fin de semana vie 17:00 → dom 18:00) y una posición que cruza el break
quedaría fail-closed PARA SIEMPRE. **Decisión: conteo por CALENDARIO DE
SESIÓN** — predicado puro nuevo `mercado_abierto_et` (CME Globex base:
dom 18:00 → vie 17:00, break diario 17–18 ET) + `barras_esperadas(inicio,
fin, tf)` que solo espera slots abiertos, con `REARM_TOLERANCIA_BARRAS = 1`
(bordes de rejilla/barra en formación — nombrada). Por qué no reusar
`sesion_et`: es una PARTICIÓN DE DISPLAY (RTH/tarde/asia/europa), no un
calendario de apertura. **Feriados CME: no modelados a propósito** — un
feriado produce "hueco" y el job no re-arma ese día (fail-closed honesto,
lado seguro); un hueco REAL de 30 min en horario vivo (6 barras > 1 de
tolerancia) JAMÁS pasa como legítimo — ambos lados con test.

Comportamiento con mercado CERRADO (documentado): durante el break la barra
más nueva es legítimamente vieja → frescura falla → None (no se infiere con
el mercado cerrado — tampoco hay fills posibles); tras la reapertura el
conteo por calendario revalida el tramo (test del cruce).

## Wrapper no-puro mínimo

`obtener_inferencia(market_data, symbol, opened_at, timeframe, now=None)` —
UNA función: `get_bars` (límite = esperadas+100) + `get_atr`, y DELEGA todo
el juicio a los puros. `heartbeat_max_age` = el MISMO
`NTBRIDGE_HEARTBEAT_MAX_AGE` de L1.6 (P2: fuente única, jamás umbral
propio). `now` inyectable solo para tests deterministas. None ⇒ el job
registrará `REARM_SKIP{feed_hueco}` (sub-paso 5/6).

## Tests (`tests/test_rearm_inferencia_ra2b3.py`, 21 — fechas
auto-comprobantes con weekday())

Cruce de mantenimiento ⇒ VÁLIDO (5+7 barras exactas) · cruce de fin de
semana ⇒ VÁLIDO · hueco real 30m en vivo ⇒ None · high None ⇒ None · time
basura ⇒ None · sin barras / timeframe raro ⇒ None · barra vieja ⇒ None (y
el borde exacto tf+hb ⇒ válido) · mercado cerrado ⇒ None (documentado) ·
toques por lado con frontera exacta INCLUSIVE (limit == min_low ⇒ tocado) ·
backstop/TP con lados invertidos correctos y None ⇒ False · ATR con umbral
estricto y None ilegible · bordes del calendario (16:55/17:00/18:00,
viernes tarde, sábado, domingo) · conteo esperado exacto · wrapper feliz /
hueco / sin barras.

## Suite

74 verdes en local (21 nuevos + sub-paso 2 intacto). Suite completa: ver
cierre. Pendiente: sub-paso 4 (motor R-RA9 puro) → 5 (RearmJob) → 6
(audit + adversariales). Commit del arquitecto (Protocolo §0).
