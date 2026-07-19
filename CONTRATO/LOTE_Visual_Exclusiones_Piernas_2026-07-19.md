# LOTE VISUAL-EXCLUSIONES + PIERNAS-CLARIDAD — display, sin tocar cálculo

> 2026-07-19 · Hallazgos del operador 2026-07-18/19 · Solo
> `strategy_detail.html` (JS de la ficha) + guarda nueva
> `tests/test_visual_exclusiones.py`. Cero cambios en motor/estudio.

## Parte 1 — Lenguaje ÚNICO de exclusión en el recorrido

**Qué había:** los toggles de día/sesión atenuaban las barras excluidas
(alpha 0.30), pero la exclusión por DIRECCIÓN no: con solo-largos, el corto
MAE −$11,849 (el #22 de ES_CS) seguía a todo color siendo la barra dominante
— el gráfico contradecía al contador ("+1 EXCLUIDA por dirección").

**Fix (drawChart):**
- UNA definición de "fuera de la fila": `excl = día ∨ sesión ∨ ¬dirección` —
  toda barra excluida se atenúa con el MISMO estilo (alpha 0.30). El contador
  del SL (BUG-SL-INSENSIBLE) ahora consume esa MISMA `excl` — contador y
  atenuado no pueden volver a divergir.
- **Tooltip** por barra atenuada (title nativo del canvas, hit-test por
  columna): "excluida de la fila — dirección: solo largos / sesión apagada /
  día apagado". Bind una sola vez; datos por render en `cv._lxHit`.
- **Contención LX-13**: no hay barra que atenuar — los `no_contenido` se
  excluyen aguas arriba (jamás entran a la nube del dashboard) y los declara
  el banner de muestra. Documentado en el comentario del render.

**Decisión de escala (con justificación, como pedía el lote):** la escala
del eje SÍ sigue incluyendo las barras atenuadas. Razones: (1) la barra
sigue dibujada — sacarla del eje recortaría sus propios píxeles; (2) mover
un toggle no hace saltar la escala (comparación estable antes/después);
(3) para la distorsión ya existe el toggle "escala: recortada (p95)"
(CHARTCLIP). La nota vive en el title del toggle de escala: "la escala
incluye también las barras atenuadas… si una excluida domina el eje, usa la
escala recortada (p95)".

## Parte 2 — Piernas/Re-armado: claridad

1. **Conversión ATR↔$** — header nuevo: "1 ATR (mediana del estudio) ≈ $X
   (· pts / FX: ~N ticks vía `luxyFmtPts`, espejo de `fmt_pts`)" +
   equivalente $ junto a CADA profundidad ("C2 · 3.85×ATR ≈ −$1,9xx") en el
   header y en la curva de llegada. Fuente: `D.units.atr_med_pts × PV` (la
   mediana DEL ESTUDIO — coherente con el ancla).
2. **Ancla declarada**: "⚓ Anclado a la escalera del ESTUDIO (C2/C3/reparto)
   — se recalcula con Calcular estudio; NO sigue las palancas del panel" +
   **chip ámbar** "⚠ tus palancas actuales difieren de la escalera del
   estudio", encendido por `refresh()` comparando C1/C2/C3 actuales contra
   RECO0 (las del estudio) — vive al lado del ancla y reacciona en vivo al
   mover sliders (el panel en sí sigue estático, como es).
3. **Jerarquía**: el recuadro del VEREDICTO (🟢/🔴/⚪ + constantes RA-2 con
   evidencia) SUBE al tope del panel, justo bajo ancla+conversión;
   curva de llegada / tabla de oro / graduada / muerte-ciega-régimen quedan
   debajo como evidencia. La línea "Base: N trades contenidos…" cierra.

## Guardas (tests/test_visual_exclusiones.py — 11 tests)

- Fuente: `excl` incluye dirección · atenuado usa `excl` · tooltip con los 3
  motivos · contador comparte la definición · nota de escala presente ·
  conversión ATR↔$ · ancla + chip (y su driver en `refresh` vs RECO0) ·
  veredicto ANTES de curva y tabla de oro, sin bloque duplicado.
- **Sintaxis JS real en node** de las 4 funciones tocadas (drawChart,
  piernas, refresh, legend): extracción por conteo de llaves + `new
  Function` en node (skip sin node; input UTF-8 explícito — cp1252 de
  Windows revienta con −/—).
- Regresión previa intacta: test_lx15* (JS + render HTML), display-fx, web.

## Pendiente

- **Smoke del operador** (mandato del lote antes del commit): ver la ficha
  con solo-largos → el corto dominante debe verse tenue con tooltip; panel
  Piernas → veredicto arriba, conversión $ y chip ámbar al mover C2/C3.
- Commit del arquitecto tras el smoke (Protocolo §0).
