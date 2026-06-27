# Anexo 15 — Metodología de calibración por instrumento + prompt para Claude Code+TradingView · v1.0

**Fecha:** 2026-06-26
**Ámbito:** Replicar el análisis de ES (Anexos 11–14) en los otros 7 instrumentos de la Fase 1.
**Estado:** Metodología y plantilla de prompt. Pendiente de ejecutar por instrumento.

---

## 0. Objetivo

ES quedó calibrado (Anexos 11–14): señal cruda LuxAlgo en RTH + **SL 2.5×ATR** + TP 6.0
(bracket) + guardarraíles símbolo/timeframe, sin QualityScorer ni HMM. Este anexo define
**cómo reproducir ese mismo análisis** en los 7 instrumentos restantes para fijar su
`sl_atr_multiplier`, su ventana operativa y si alguna capa opcional (QualityScorer/HMM)
aporta valor.

El análisis de ES fue ad-hoc en una sesión que no quedó versionada. Esta plantilla lo
estandariza para que cada instrumento produzca resultados **comparables**.

---

## 1. Alcance — los 8 instrumentos de la Fase 1 (operados en micro)

| # | LuxAlgo / chart (estándar) | Micro a operar | Clase | Ventana inicial a TESTEAR (ET) |
|---|---|---|---|---|
| 1 | `CME_MINI:ES1!` | **MES** | Índice | RTH 09:20–15:45 ✅ (validado, Anexo 11) |
| 2 | `CME_MINI:NQ1!` | **MNQ** | Índice | RTH 09:30–15:45 (hipótesis) |
| 3 | `CBOT_MINI:YM1!` | **MYM** | Índice | RTH 09:30–15:45 (hipótesis) |
| 4 | `CME_MINI:RTY1!` | **M2K** | Índice | RTH 09:30–15:45 (hipótesis) |
| 5 | `COMEX:GC1!` | **MGC** | Metal | Pit 08:20–13:30 (hipótesis) |
| 6 | `NYMEX:CL1!` | **MCL** | Energía | Pit 09:00–14:30 (hipótesis) |
| 7 | `CME:6J1!` | **MJY** (micro Yen) | FX | ~24h 18:00–17:00 (hipótesis) |
| 8 | `CME:6E1!` | **M6E** | FX | ~24h 18:00–17:00 (hipótesis) |

> **Regla micro/estándar:** el backtest de LuxAlgo corre sobre el **estándar** (no hay
> estrategias micro). Las métricas en **dólares se dividen entre 10** para el micro; las
> **porcentuales no cambian** (Anexo 08 §9d). El símbolo a operar siempre es el micro explícito
> (nunca "quitar la M"; recordar MJY ≠ M6J).

> **La ventana NO se asume.** En ES, RTH mejoró la calidad (PF 1.81→2.03). Para los demás hay
> que **probar** la ventana, no copiarla: FX y energía tienen perfiles intradía distintos a los
> índices. La tabla de arriba son hipótesis de arranque.

---

## 2. Los 7 pasos (lo que se hizo con ES, a replicar por instrumento)

Para cada instrumento, en TradingView con el LuxAlgo Backtester (S&O) cargado en 5m:

1. **Conexión y estado del chart** — verificar CDP, símbolo, TF=5m, estudios cargados, quote.
2. **Ingeniería inversa de la estrategia** — leer inputs del Backtester, decodificar
   `@long/@short/@exit_all` (confirmar el carácter de la estrategia: en ES es contra-tendencia
   en rango). Confirmar que las EMAs son visuales y que el Pine está protegido.
3. **Backtest — Key stats** — transcribir Net, Win%, PF, # trades, Max DD; mismo periodo que ES
   (15-mar a 25-jun 2026) para comparabilidad, o el máximo disponible (anotar el rango real).
4. **Exportar CSV de la Lista de operaciones** — con **MAE/MFE por trade** (insumo crítico del
   barrido de SL). Guardar como `REPORTES/trades_<SIMBOLO>_<rango>.csv`.
5. **MAE de ganadores** — media/mediana en puntos y en ×ATR; % de ganadores que sufren >1.5×ATR
   en contra (define dónde "muerde" un SL fijo).
6. **Señales históricas + alertas** — tabla de señales recientes; estado de alertas.
7. **Dos simulaciones cuantitativas** sobre el CSV:
   - **A)** Backtest por ventana: comparar 24h vs la(s) ventana(s) candidata(s).
   - **B)** Barrido de SL {1.5, 2.0, 2.5, 3.0, 4.0}×ATR con TP 6×ATR → identificar el óptimo y
     confirmar/descartar el 1.5× por defecto.

**Mejora vs ES:** usar **ATR(14) real por barra** desde los HOLC locales
(`NINJATRADER/HOLC/<SIMBOLO>_5m.csv`, ya disponibles para los 8) en lugar del proxy fijo de
7 pts. Esto hace el barrido de SL más exacto (los HOLC son la misma fuente de `ohlcv_bars`).

**Opcional (solo si el barrido de SL deja edge que defender):** repetir la evaluación de
QualityScorer (Anexo 13) y de régimen HMM (Anexo 12) con el método point-in-time + OOS. En ES
ambos se descartaron; en otros instrumentos podrían aportar y deben evaluarse aparte.

---

## 3. Entregable por instrumento

Un mini-anexo `CONTRATO/1X_CALIBRACION_SL_ATR_<SIMBOLO>_v1_0.md` con la misma estructura del
Anexo 11: motivo, fuente de datos, metodología, tablas (24h vs ventana; barrido de SL),
hallazgos, **decisión** (`sl_atr_multiplier`, ventana, tp), caveats. Y la aplicación en
`StrategyProfile` (o seed) del nuevo multiplicador.

---

## 4. PROMPT PLANTILLA para Claude Code+TradingView

> Copiar/pegar reemplazando `<SIMBOLO_TV>` y `<MICRO>`. Un instrumento por corrida.

### 4.0 Dos reglas que NO se pueden saltar

1. **USAR LA PESTAÑA YA CONFIGURADA DEL INSTRUMENTO.** Hay **una pestaña por activo**, cada una
   con los indicadores y la configuración del LuxAlgo Backtester **exclusivos de ese activo**.
   Claude Code+TV NO debe abrir un chart nuevo, NO debe cambiar el símbolo de ninguna pestaña, y
   NO debe usar la primera pestaña (es ES). Debe **listar las pestañas, encontrar la del símbolo
   correcto y activarla**. Si cambia el símbolo de la pestaña de ES, hereda la config de ES y el
   backtest sale con parámetros equivocados (incidente real 2026-06-26 con NQ).
2. **El barrido de SL lo hace Cowork con ATR(14) REAL**, no Claude Code con proxy. El proxy fijo
   (p. ej. 86 pt en NQ) infló el ATR ~2.4–4× y dio un óptimo de SL falso. La tarea de Claude
   Code+TV es **exportar el trade-list correcto** desde la pestaña del instrumento; el barrido
   sobre `NINJATRADER/HOLC/<SIMBOLO>_5m.csv` se corre con `scripts/calibrate_sl_from_trades.py`.

### 4.1 Prompt

```
Contexto: Estoy calibrando NTEXECG instrumento por instrumento. Ya cerré ES (CME_MINI:ES1!).
Ahora replica el mismo análisis para <SIMBOLO_TV> (se operará en su micro <MICRO>). El backtest
corre sobre el contrato estándar; las métricas en dólares se dividen entre 10 para el micro, las
porcentuales no cambian.

CRÍTICO — USA LA PESTAÑA YA CONFIGURADA, NO ABRAS NI MODIFIQUES OTRA:
Tengo UNA pestaña por instrumento, cada una con los indicadores y la configuración del LuxAlgo
Backtester EXCLUSIVOS de ese activo. NO abras un chart nuevo. NO cambies el símbolo de ninguna
pestaña. NO uses la primera pestaña (es ES). Procede así:
  1. Lista TODAS las pestañas abiertas con su símbolo.
  2. Activa la pestaña cuyo símbolo sea <SIMBOLO_TV>.
  3. Confirma que la pestaña activa es <SIMBOLO_TV> en 5m y que el "LuxAlgo Backtester (S&O)"
     está cargado CON SU configuración propia.
  4. Si NO existe una pestaña de <SIMBOLO_TV>, DETENTE y avísame. No improvises con otra pestaña
     ni cambies símbolos.

MODO AUTÓNOMO: ejecuta todo de corrido, sin hacerme preguntas ni pedir confirmación entre pasos.
Toma decisiones razonables y anótalas. Si algo falla (salvo la regla de la pestaña), regístralo y
sigue. Entrega solo el reporte final.

Pasos:
1. ESTADO: tv_health_check; confirma pestaña/símbolo/TF/estudios y la cotización.
2. ESTRATEGIA: lee TODOS los inputs del Backtester de ESTA pestaña y decodifica
   @long/@short/@exit_all. Dime el carácter (tendencia/contra-tendencia/rango) y las
   confirmaciones. Indica si las EMAs alimentan la señal o son visuales y si el Pine está protegido.
   IMPORTANTE: reporta la config EXACTA de esta pestaña (puede diferir de ES).
3. KEY STATS: Net, Win%, PF, # trades, Max DD del panel. Mismo periodo que ES
   (15-mar a 26-jun 2026) si está disponible; si no, el máximo, y anota el rango exacto.
4. CSV (lo más importante): exporta la Lista de operaciones COMPLETA con MAE/MFE por trade.
   Guárdala en C:\NTEXECG\ListaDeOperaciones\ y dime el nombre del archivo y el nº de trades.
   Confirma el multiplicador $/pt detectado.
5. SEÑALES + ALERTAS: tabla de señales de los últimos ~10 días (hora NY, precio, dirección) y
   estado de alertas.

NO corras tú el barrido de SL (lo hago yo con ATR real). NO toques ninguna config de NTEXECG.
Entrega el reporte + la ruta del CSV.
```

### 4.2 Barrido de SL en Cowork (después de recibir el CSV)

```
python -m scripts.calibrate_sl_from_trades \
  --trades "ListaDeOperaciones/<archivo del instrumento>.csv" \
  --bars   "NINJATRADER/HOLC/<SIMBOLO>_5m.csv" \
  --point-value <valor $/pt del estándar> --tz-shift 0
```

Salida: ATR(14) real medio/mediano en las entradas; por ventana (24h, RTH, AM, PM, overnight) el
resultado nativo y el barrido k∈{1.5…8} con WR/PF/exp/net/maxDD y nº de stops. `--point-value`:
ES 50, NQ 20, YM 5, RTY 50, GC 100, CL 1000, 6E 125000, 6J 12500000 (verificar con el CSV).

### 4-bis. Pendientes/caveats conocidos del método
- **Payload real de alerta**: si no hay alerta configurada, no se puede leer.
- **Pine protegido**: la lógica se infiere de los inputs, no del código.
- **Desglose por día de semana**: requiere muestra suficiente; anotar si no alcanza.
- **Alineación de zona horaria** CSV↔HOLC: si el ATR medio sale raro, ajustar `--tz-shift`.

---

## 5. Orden sugerido y comparabilidad

1. Mismos índices que ES primero (**NQ, YM, RTY**) — perfil más parecido, valida rápido si la
   conclusión "señal cruda + SL ~2.5×ATR en RTH" generaliza.
2. Luego **GC y CL** (metal/energía, ventana distinta).
3. Al final **6J y 6E** (FX casi 24h; la "ventana" cambia de naturaleza).

Mantener **mismo periodo, mismo set de k×ATR y mismo TP 6×ATR** en todos para poder comparar.
Consolidar al final una tabla maestra: instrumento → ventana → sl_atr_multiplier → PF.

---

## 6. Caveats transversales
- Muestras chicas (≈120 trades / 3 meses por instrumento) → PF ruidoso; tratar como dirección,
  no verdad fina. Validar OOS antes de cualquier filtro adicional.
- Backtest sin comisiones/slippage. El micro reduce el riesgo $ pero la comisión pesa más en %.
- El SL obligatorio cuesta edge (en ES ~35%); se asume como control de riesgo en fase paper.
```
