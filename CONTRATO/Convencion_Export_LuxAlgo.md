# Convención de export de listas de operaciones (LuxAlgo/TradingView) — NTEXECG

> Cierra el punto R5 del contrato (integridad de exports). Regla operativa para el
> operador al re-exportar la lista de operaciones de una estrategia. Escrito 2026-07-10.

## Por qué importa

Todo el estudio de riesgo (Motor + Lab) se computa **sobre el CSV de operaciones** de LuxAlgo.
Un export mal tomado degrada el estudio **sin romper nada visible**. El caso que lo enseñó fue
**6J**: un export nuevo llegó con una ventana MÁS CORTA que el anterior (64 vs 78 trades) — menos
datos, estudio más pobre, y nadie se entera si no se compara.

## Reglas

1. **Un export = una estrategia = una lista.** Cada estrategia tiene su propio CSV. Las ventanas
   NO tienen que coincidir entre estrategias (cada estudio in-sample es independiente); lo que
   importa es la integridad DENTRO de cada estrategia.

2. **Nunca encoger la ventana.** Al re-exportar para refrescar datos, la ventana nueva debe ser
   **igual o superconjunto** de la anterior (misma fecha de inicio o anterior, hasta hoy). **Un
   export con menos trades que el vigente es la señal de alarma** — casi siempre es un error de
   exportación (rango de fechas mal puesto), no menos operaciones reales.

3. **Más historia es bueno** (para un estudio in-sample). Ejemplo: RTY cubre desde 2025-09
   (107 trades) mientras otras arrancan en 2026-03 — se **deja así a propósito** (más datos =
   estudio más robusto). No uniformar ventanas a costa de perder trades.

4. **Excepción — datos inválidos/mezclados.** Solo se re-exporta con ventana MÁS CORTA si el
   tramo viejo corresponde a una **versión distinta de la estrategia** o a un **contrato anterior**
   (datos que ensucian el estudio). En ese caso, re-exportar limpio desde el punto válido — y
   anotarlo aquí.

## Flujo al re-exportar

1. Exporta la lista desde LuxAlgo/TradingView con el rango de fechas **completo** (desde el inicio
   válido, hasta hoy).
2. Súbela por **Riesgo → Reemplazar lista → Subir e integrar** de esa estrategia. Con LAB-1, un
   solo upload **refresca Motor y Lab** (encola el recalc del Lab).
3. Verifica:
   - El **cuadre al dólar ✅** y el n de trades en la cabecera del estudio (no debe bajar respecto
     al vigente salvo la excepción 4).
   - El badge **"difiere / aplicada"** del Puente (si cambió la reco, re-aplicar es 1 clic con diff).
   - En el server, la reconciliación Lab↔Motor:
     ```
     cd ~/ntexecg && PYTHONPATH=. .venv/bin/python scripts/lab_motor_reconcile.py
     ```
     Debe seguir en **N/N coinciden, 0 difieren** (las únicas diferencias esperadas son los trades
     sin cobertura ATR, que el reconcile reporta aparte).

## Estado por estrategia (2026-07-10)

- **ES, NQ, GC, 6E, 6J, ES_ConfStrong** — export 070726, ventana desde 2026-03.
- **RTY** — ventana larga desde 2025-09 (107 trades) — **intencional, se deja** (mejor net, más
  historia; Lab↔Motor coinciden).
- **CL, YM** — instrumentos legacy (no son estrategias activas de la demo).
