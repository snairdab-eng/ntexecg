# AUDITORÍA DE SÍNTESIS — ¿La lógica apunta a la misión? · 2026-07-07

> Síntesis del arquitecto saliente (Fable) desde todo lo explorado y
> validado 2026-07-05→07 (auditorías por pestaña, lotes R-obs y Puente,
> activación demo). No es una re-exploración: es el mapa completo evaluado
> contra la misión.

## 0. La misión (contra la que se audita)

NTEXECG NO existe para mejorar la señal (el edge es de LuxAlgo). Existe
para que **ningún trade catastrófico se lleve la cuenta**, con palancas de
riesgo validadas fuera de muestra (backstop fijo, escalera, TP nominal,
gestión por lado), **sin saltarse señales** (participación 100%),
**fail-closed** en toda la ruta de despacho, y **solo paper/demo**.

## 1. Veredicto por módulo

| Módulo | Veredicto | Nota |
|---|---|---|
| Pipeline L1–L5 | ✅ ALINEADO | Fail-closed real (entrada sin bracket → BLOCK), guarda P0 activa, kill-switch por capas, symbol_busy, guardarraíles con bloqueos reales y trazables por nivel. Es la ruta crítica y está sana. |
| Motor de Riesgo | ✅ ALINEADO (el corazón) | Gate OOS decide, corte cancel_after modela los fills REALES, TP nominal p99 ("que cierre LuxAlgo"), participación 100% obligatoria (R-obs-2 mató el "sobrevivir sin operar"), determinismo bit-a-bit verificado. |
| Puente Riesgo→Estrategias | ✅ ALINEADO | Única puerta de escritura a producción, supervisada (diff+confirmación+AuditLog), deriva visible, promoción con identidad única. Cerró la deriva invisible que encontró la auditoría del 07-06. |
| Config viva + dispatch multi-perfil | ✅ ALINEADO (tras R-obs-2c) | Bracket completo (fijo+nominal) heredado por los 4 perfiles y niveles; recompute por destino con guarda P0 espejada y fallback fail-closed al bracket del L5. |
| Activación demo | ✅ VIVA | 7/7 armadas con brackets del estudio; smoke de cadena completa OK (señal→L5→payload→TradersPost 200 multi-perfil). |
| Lab | ⚠ DESALINEADO EN DATOS | Filosofía correcta (explora la señal, solo lectura hacia producción — topología definida), pero HOY sus 7 cachés muestran datos viejos: para decidir, es información inservible. Lotes LAB-1..3 escritos. |
| Dashboard | ⚠ PARCIAL | KPIs/charts sanos; como pantalla de vigilancia de la demo le faltan posiciones/entregas-con-bracket/deriva y arrastra columnas muertas y el FX 0.00. Lotes DASH-1/2 escritos. |
| Activos | ⚠ PARCIAL | Capa de herencia legítima con información muerta (defaults sin herederos, filas sin uso) y sin conexión con los bloqueos N2 que sus ventanas producen. Lote ACT-1 escrito. |
| Filtros L4 / Régimen | ✅ coherentes con el pivote / ⚠ régimen | Dormidos por diseño (edge = LuxAlgo). Riesgo de adorno silencioso en régimen si el bridge no exporta 1h (verificación EST-1). |

## 2. Desvíos residuales contra la misión (priorizados)

**R1 — Ventanas L2 que recortan señales (anti-misión DIRECTA).** 9 de los
26 bloqueos de la última semana son N2 Temporal. Si el backtest de una
estrategia genera trades en horas que su ventana vigente no cubre, el
gateway está FILTRANDO señales del edge — exactamente lo que la misión
prohíbe. Es el pendiente de mayor prioridad: lote **RIES-W** (ventana de
cobertura desde el estudio + % de trades fuera de la ventana vigente).

**R2 — cancel_after NTEXECG↔TradersPost es un acuerdo humano sin
verificación.** El sistema no puede leer la config de TradersPost; si un
lado cambia y el otro no, la escalera vive con una caducidad que el
estudio no asumió, en silencio. Mitigación actual: avisos en ficha/diff.
Mejora barata futura: campo manual "confirmado en TradersPost el <fecha>"
por estrategia, visible junto al bracket.

**R3 — Lab stale como fuente de decisiones erróneas.** Mientras LAB-1 no
se implemente, cualquier lectura del Lab debe considerarse vieja por
defecto (las cachés son del 07-04 con exports anteriores).

**R4 — Cuenta $10k vs instrumentos grandes: límite ESTRUCTURAL, no bug.**
La protección de ES lo dice honesto: ni el mejor combo al 100% deja el
peor trade ≤10% de una cuenta de $10k (peor histórico ~$10k por mini).
Eso es una decisión de negocio pendiente del operador: subir cuenta,
bajar tamaño total (menos micros), o aceptar el % — el sistema ya muestra
el costo de cada opción con la cuenta editable por estrategia.

**R5 — Integridad de los exports de entrada (el eslabón más frágil).**
Todo el edificio computa sobre el CSV de LuxAlgo: el caso 6J (export
nuevo con ventana MÁS CORTA que el anterior: 64 vs 78 trades) muestra que
un export mal exportado degrada el estudio sin romper nada visible. El
motor ya avisa (superconjunto/faltantes); la disciplina de export
(misma ventana, siempre completa) es del operador. Recomendación:
documentar la convención de export en CONTRATO y revisar el aviso de
superconjunto para que un encogimiento de ventana sea IMPOSIBLE de pasar
por alto.

**R6 — Menores, ya en lotes**: escalera ES_ConfStrong diferida
(design_only, decisión con fills reales); columnas muertas del bridge;
override Avanzado por perfil puede degradar el bracket del estudio en un
destino (explícito y documentado — vigilar en demo).

## 3. Lo que la demo debe demostrar (checkpoints de los próximos días)

1. Primera ENTRADA aprobada post-aplicación con bracket nuevo en el
   payload (stop fijo exacto + TP nominal del lado).
2. Escalera: piernas límite que llenan/cancelan dentro del cancel_after —
   comparar fills reales vs la tabla de corte del estudio (la primera
   validación empírica del modelo de fills).
3. Un trade perdedor real capado por el backstop (la razón de ser del
   sistema — cuando ocurra, auditar el ciclo completo).
4. Cero envíos sin bracket, cero órdenes fuera de paper.

## 4. Orden global de lotes pendientes (prompts ya escritos en CONTRATO/)

RIES-W (misión directa) → LAB-1 (datos frescos) → DASH-1 → EST-1 →
ACT-1 → LAB-2 → DASH-2 → EST-2 → LAB-3.

## 5. Veredicto global

**La ruta crítica de la misión — señal → pipeline fail-closed → bracket
del estudio → despacho paper multi-perfil — está alineada, verificada y
viva.** Los desvíos residuales están en (a) las superficies de
OBSERVACIÓN (Lab/Dashboard/Activos), que muestran datos viejos o vacíos
pero no tocan órdenes, y (b) dos dependencias HUMANAS documentadas
(cancel_after en TradersPost, disciplina de exports). El único desvío que
toca la misión directamente es R1 (ventanas que filtran señales) y ya
tiene lote diseñado. El sistema que despacha dinero (paper) hoy hace
exactamente lo que la misión pide; lo que queda es hacer igual de
confiable lo que el operador LEE para decidir.
