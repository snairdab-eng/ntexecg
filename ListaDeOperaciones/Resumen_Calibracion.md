# Resumen de Calibración NTEXECG — LuxAlgo Backtester (8 instrumentos)

**Fecha:** 27-jun-2026 · **Estrategia base:** LuxAlgo® Backtester (S&O), Pine protegido `PUB;bd27017692354be0877227c3b822dcdd` v38
**Método:** config leída en vivo de cada pestaña propia + CSV autorizado por instrumento en `C:\NTEXECG\ListaDeOperaciones\`. Barrido de SL y MAE con regla conservadora (MAE>k×ATR ⇒ stop en −k×ATR; si no, resultado real con TP 6×ATR). Métricas $ = contrato estándar; micro = ÷10.

> ⚠️ **ATR proxy:** calculado de velas recientes por instrumento. En **GC, 6E y 6J** la ventana era de bajo volumen → sus ×ATR y barridos de SL son **aproximados; recalcular con ATR(14) real antes de fijar SL**.
>
> ✅ **Validación 27-jun:** las **8 pestañas verificadas en vivo** (condición `@long/@short`, TF, estudios) **coinciden con sus reportes**. CSVs ayer-vs-hoy: 6 idénticos, ES +2 trades, CL +1 (artefacto de roll, no operable). **Único cambio de fondo: 6J** — el export de ayer (26 trades) estaba incompleto por warmup del OscMatrix; el correcto es **77 trades** (config sin cambios), ya reflejado aquí.

---

## Tabla maestra

| Inst | TF | $/pt | Micro | Lógica / indicador | Régimen | Trades | WR% | PF | Net std | Net micro | MaxDD std | Calmar |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **RTY** | 15m | 50 | M2K | tendencia (Conf+Neo) | trending | 112 | 86.6 | 2.15 | $44,620 | $4,462 | $7,465 | **6.0** |
| **GC** | 5m | 100 | MGC | contrarian+SmartTrail+confluence (OscMatrix) | — | 106 | 60.4 | 1.91 | $128,970 | **$12,897** | $38,690 | 3.3 |
| **CL** | 15m | 1000 | MCL | contrarian+catcher (OscMatrix) | trending | 103 | 79.6 | 2.08 | $41,700 | $4,170 | $10,420 | 4.0 |
| **ES** | 5m | 50 | MES | fade en rango (Confirmation) | ranging | 119 | 82.4 | 1.81 | $34,450 | $3,445 | $11,750 | 2.9 |
| **NQ** | 5m | 20 | MNQ | pullback (Conf+SmartTrail) | — | 64 | 84.4 | 1.54 | $33,030 | $3,303 | $35,885 | 0.9 |
| **YM** | 15m | 5 | MYM | contrarian extremos (OscMatrix) | — | 48 | 89.6 | 1.92 | $22,690 | $2,269 | $9,175 | 2.5 |
| **6E** | 5m | 125k | M6E | confluencia/momentum (Conf+ +Neo+Confl) | — | 99 | 84.8 | 1.44 | $3,662 | $366 | $2,994 | 1.2 |
| **6J** | 5m | 12.5M | — | contrarian+tracer+moneyflow (OscMatrix) | — | 77 | 93.5 | 3.99 | $3,825 | $382 | $675 | 5.7 |

*(Net micro = lo que realmente operas. 6J corregido 27-jun: el export inicial de 26 trades estaba incompleto por warmup; el set correcto es 77 trades.)*

---

## Configuración recomendada por instrumento

| Inst | Ventana operativa | sl_atr_multiplier | ¿Señal cruda basta? | QualityScorer/HMM |
|---|---|---|---|---|
| **ES** | **RTH 09:20–15:45** | **2.5×ATR** | Sí | no |
| **NQ** | **24h** (no RTH) | **4.0×ATR** | No | **recomendado** |
| **YM** | **24h** (no RTH) | nativo / 4.0× | Sí | si se fuerza RTH |
| **RTY** | **RTH (AM 09:30–12:00 🔥)** | nativo / 4.0× | Sí | no |
| **GC** | **24h** o **PM 12:00–15:45** | nativo / ≥3× ⚠️ recalcular | Sí (net) | **evaluar (cola)** |
| **CL** | **24h** (no RTH) | nativo / 2.0× | Sí | si se fuerza RTH |
| **6E** | **RTH (AM 🔥)** | 4.0× | Sí | no |
| **6J** | 24h | nativo (recalcular ATR) | Sí (WR 93.5%) | innecesario |

---

## Hallazgos universales (válidos en los 8)

1. **El SL de 1.5×ATR es el PEOR punto en TODOS los instrumentos.** Sin una sola excepción. El bracket obligatorio del gateway debe ser más holgado.
2. **La salida nativa por señal iguala o supera a cualquier SL fijo** en casi todos; cuando el gateway obliga a stop, **más ancho es mejor** (2.5×–4×ATR), salvo CL (k=2) y ES (k=2.5).
3. **La ventana RTH solo funciona en ES, RTY y 6E.** En NQ, YM, CL (y GC parcialmente) el edge está en **24h/overnight**; su RTH es negativo o flojo. **El régimen del filtro NO predice la ventana** (RTY y CL ambos "trending", pero RTY brilla en RTH y CL no).
4. **Cada pestaña usa indicador/lógica/TF/régimen DISTINTOS** → no se puede clonar config entre instrumentos (confirmado 8 veces). La contaminación ES→NQ de la primera corrida lo demostró por la vía dura.
5. **El TP de 6×ATR casi nunca dispara** (0–6 hits) → es prácticamente inerte; considerar salida por señal o TP más corto.
6. **ATR proxy** poco fiable en GC/6E/6J (ventanas quietas) → recalcular con ATR(14) real antes de fijar SL en esos tres.

---

## Priorización de cartera (sugerida)

- **Núcleo (mejor riesgo/retorno):** **RTY** (Calmar 6.0, RTH/AM), **CL** (Calmar 4.0, 24h), **GC** (mayor net micro $12.9k, pero alta cola → candidato a filtro).
- **Sólidos:** **ES** (RTH, 2.5×ATR — config ya cerrada), **YM** (24h), **NQ** (24h + stop ancho; el de peor Calmar, 0.9 — vigilar).
- **Marginales por $ (no por calidad):** **6E** (RTH, ~$366 micro) y **6J** (77 trades, estadísticamente sólido —WR 93.5%, Calmar ~5.7— pero retorno absoluto pequeño ~$382 micro).

**Acciones pendientes recomendadas:**
1. Recalcular barrido de SL con **ATR(14) real** en GC, 6E, 6J.
2. Evaluar **QualityScorer/HMM** en NQ (RTH falla) y GC (riesgo de cola: trade −$15k).
3. Re-correr cada backtester con **filtro de sesión** activado en su ventana recomendada para validar métricas reales de ejecución del gateway.

---

## Archivos
Reportes individuales en Descargas: `Reporte_{ES,NQ,YM,RTY,GC,CL,6E,6J}_LuxAlgo.md`
CSVs autorizados: `C:\NTEXECG\ListaDeOperaciones\` (8 instrumentos)

*Generado en sesión de calibración NTEXECG.*
