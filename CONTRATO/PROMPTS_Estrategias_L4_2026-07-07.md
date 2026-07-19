# Estrategias: Guardarraíles / Filtros de calidad / Régimen · 2026-07-07

> Evaluación del arquitecto saliente (Fable) para que **Opus 4.8** implemente.
> Pregunta del operador: ¿estas secciones funcionan o son adorno? ¿Están
> conectadas a datos reales y a la evidencia del Lab donde aplique?

## A. Evaluación (código + producción, 2026-07-07)

1. **Guardarraíles (Anexo 08) — FUNCIONAN.** Cableados en L1/L2 del
   pipeline, con suite dedicada (`tests/test_guardrails_anexo08.py`) y
   bloqueos reales esta semana (N1=11, N2=9 de 26). El detalle de decisión
   muestra el nivel que bloqueó y los posteriores como "no evaluados" —
   trazabilidad correcta. Nada que arreglar de fondo.

2. **Filtros de calidad (Fase 5) — FUNCIONALES pero invisibles.** El
   QualityScorer computa con barras 5m REALES del bridge (volumen incluido)
   y compara contra score_minimum en L4. Hoy están todos desmarcados
   (correcto con el pivote: el edge viene de LuxAlgo) → score 100, nunca
   bloquean. El problema de percepción es real: la UI no muestra NINGUNA
   evaluación viva (ni el score de la última señal ni qué daría cada
   filtro), así que es imposible distinguir "funciona y está dormido" de
   "adorno". Además la única evidencia para decidir activarlos —el estudio
   del Lab con lift OOS de ESTOS MISMOS filtros— no aparece en la ficha.

3. **Régimen (Fase 6) — RIESGO DE ADORNO SILENCIOSO.** No depende de
   hmmlearn (baseline determinista Kaufman-ER, mismo interfaz), pero
   clasifica sobre BARRAS 1h del bridge y "unknown nunca bloquea"
   (fail-open). Si el bridge de producción NO exporta bars 1h (sospecha
   fuerte: la columna ATR 1H del dashboard lleva "—" desde siempre),
   activar el toggle no haría NADA y nadie se enteraría. Verificación en
   server obligada; si no hay 1h, el toggle debe decirlo — no fingir.

4. **Conexión Lab→Estrategias**: por decisión de topología (ver
   PROMPTS_Lab_CaminoB2 §E) es SOLO LECTURA. Lo que falta es exactamente
   esa lectura: el veredicto del Lab por filtro en la ficha.

---

## B. 📋 PROMPT PARA OPUS 4.8 — LOTE EST-1: evidencia en vivo (que se VEA que funcionan)

Eres el implementador de NTEXECG (FastAPI + Jinja2/HTMX/Alpine; solo
paper/demo). Archivos: `app/web/routes_strategies.py`,
`app/templates/strategy_detail.html`, `app/services/quality_scorer*.py`,
`app/services/hmm_service.py`, `app/services/regime_features.py`, tests.
NO commit/push. Verifica con `.venv\Scripts\python.exe -m pytest -q`
(cuelgue flaky → `-o faulthandler_timeout=300 --timeout=600`).

Tareas:
1. **Verificación 1h del régimen (primero, condiciona el resto)**: revisa
   cómo el provider del bridge sirve timeframes
   (`app/services/market_data_service.py`) y comprueba QUÉ archivos
   escribe el bridge en producción (pregunta al operador o inspecciona la
   config/documentación del bridge en el repo). Resultado:
   - Si HAY barras 1h → sigue con la tarea 3 normal.
   - Si NO hay → el toggle de régimen se renderiza deshabilitado con
     aviso claro: "⚠ no disponible: el bridge no exporta barras 1h —
     activarlo no bloquearía nada (régimen siempre unknown)". FAIL-HONEST:
     jamás un control que finge funcionar. Reporta el hallazgo.
2. **Score de calidad visible**: en la sección Filtros, muestra la ÚLTIMA
   evaluación real de esa estrategia: score y desglose por filtro de la
   decisión más reciente que llegó a L4 (inspecciona el modelo
   StrategyDecision / la etiqueta quality NX-04 para ver qué se persiste;
   si el desglose por filtro no se guarda, muestra el score y el motivo).
   Formato: "última señal: score 84 (umbral 70) — hace 2h" o "sin
   evaluaciones aún (filtros inactivos → score 100 automático)".
3. **Régimen actual visible**: junto al toggle, el régimen detectado AHORA
   para el activo de la estrategia (llamada al hmm_service con caché TTL
   60s — nada pesado por request): "régimen 1h actual: tendencia alcista
   (ER 0.42, 30 barras)" o "unknown — barras insuficientes (N<20)". Esto
   convierte el toggle en algo comprobable de un vistazo.
4. **Botón "probar ahora" (read-only)**: en Filtros, un botón que evalúa
   el QualityScorer con las barras actuales del bridge y muestra el
   desglose SIN señal ni efecto alguno (endpoint GET nuevo, solo lectura,
   con los pesos y checks del form SIN guardar). El operador puede ver el
   score que daría su configuración antes de activarla.
5. Tests: toggle deshabilitado cuando no hay 1h (mock provider sin "1h");
   última evaluación con y sin decisiones; régimen actual con barras
   suficientes/insuficientes; endpoint probar-ahora (score correcto con
   barras mock, y 409/aviso sin bridge).

Invariantes: L4 sigue opt-in y fail-open para unknown; CERO cambios de
semántica del pipeline (esto es visibilidad, no lógica); presupuesto de
queries/llamadas acotado con caché TTL; solo paper/demo. Al final:
`git diff --stat` + hallazgo del punto 1 + "LISTO PARA COMMIT" si la
suite queda verde.

---

## C. 📋 PROMPT PARA OPUS 4.8 — LOTE EST-2: veredicto del Lab por filtro (solo lectura)

Mismo marco operativo (hazlo DESPUÉS de EST-1 y de LAB-1 si es posible —
las cachés del Lab deben estar frescas para que la evidencia sirva).
Regla de topología VINCULANTE (PROMPTS_Lab_CaminoB2 §E): Lab→Estrategias
es SOLO LECTURA; nada de aplicar filtros desde aquí.

Contexto: los filtros de producción (volumen relativo, ATR normalizado,
VWAP, hora de sesión) son LOS MISMOS que el Lab estudia offline como
what-ifs con lift OOS (`lab_metrics.lift_from_rows` con subs
`volume_relative`, etc. — verifica el mapeo exacto de nombres en
`app/services/lab_metrics.py` y en el default study). El operador decide
activar un filtro EN PRODUCCIÓN; la evidencia de si aporta vive en el Lab
y hoy no se ve desde la ficha.

Tareas:
1. **Veredicto por filtro en la ficha**: junto a cada checkbox de filtro,
   una línea gris con la evidencia del Lab para ESA estrategia, leída de
   su caché (`REPORTES/lab_features_<key>.json` → el estudio default /
   oos_survivors que ya computa lifts): "Lab: Δnet OOS −$310 (no aporta)"
   o "Lab: Δnet OOS +$820, PF 1.4→1.6 (candidato — valida antes de
   activar)" o "Lab: sin caché fresca — recalcula en el Lab". Usa las
   MISMAS funciones de lab_metrics (paridad), sin recompute pesado: si el
   estudio default ya persiste los lifts en la caché/meta, léelos; si hay
   que computarlos por request, hazlo UNA vez con caché TTL o precomputa
   al regenerar la caché del Lab (elige y justifica).
2. **Stale honesto**: si la caché del Lab está stale respecto del CSV
   vigente (meta.stale), la línea lo dice — evidencia vieja no se
   presenta como actual.
3. **Link "ver estudio en el Lab →"** por estrategia (como el link_vivo
   inverso), y rotular TODO el bloque: "evidencia informativa del Lab —
   activar un filtro en producción es decisión del operador; el edge base
   viene de LuxAlgo (pivote)".
4. Tests: veredicto positivo/negativo/sin-caché/stale renderizan; el
   mapeo de nombres filtro↔sub del Lab es correcto (test unitario del
   diccionario de mapeo); cero escrituras desde estas rutas.

Invariantes: read-only absoluto; paridad lab_metrics; sin recompute
pesado por request; la decisión de activar sigue siendo 100% manual.
"LISTO PARA COMMIT" solo con suite verde.

---

## D. Orden

EST-1 → EST-2 (EST-2 idealmente tras LAB-1 para que la evidencia salga de
cachés frescas). Ambos deployables solos con el flujo de siempre.
Guardarraíles: sin lote — funcionan y tienen cobertura; cualquier cambio
ahí es innecesario hoy.
