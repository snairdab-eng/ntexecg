# Anexo 24 — Playbook de alta de estrategias por instrumento (METODOLOGÍA OFICIAL)

**Fecha:** 2026-06-29 · **Estado:** vigente · Aplica a toda alta nueva de estrategias en NTEXECG.

## 1. Filosofía (qué buscamos y qué NO)

NTEXECG **no existe para arreglar estrategias malas.** Se seleccionan estrategias que **ya son buenas**
(PF sólido, WR/expectancy positivos) cuyo único hueco es el **riesgo de cola en un desplome real**.

NTEXECG es el **gateway que controla riesgo, sube PF/ganancias y balancea la cartera**. NO tomar
estrategias de bajo PF / alto riesgo esperando "rescatarlas" (las simulaciones lo confirman: el SL
no convierte una mala en buena de forma robusta).

## 2. Rol de NTEXECG — 3 palancas

1. **SL catastrófico** (seguro de cola para el crash) — palanca universal.
2. **QualityScorer** (Nivel 4) — sube PF/neto quitando señales malas; **solo si mejora**.
3. **Decorrelación / cartera** — combinar 2-3 estrategias por instrumento que **no se solapen**.

*(El escalonado solo si la estrategia repliega consistentemente dentro del envelope; no forzarlo.)*

## 3. Criterios de selección (filtro de entrada)

- **PF nativo ≥ 1.8** (ideal ≥ 2.0).
- WR razonable / **expectancy positiva**.
- **≥ ~60 operaciones** (muestra suficiente).
- Descartar de inmediato: PF bajo, expectancy negativa, candidatas a "rescate".

## 4. SL catastrófico (definición)

- **No** usar SL ajustado ni `8×ATR(5m)` (corta ganadores en holds de varias horas).
- Fijarlo **por encima del percentil 95 del MAE nativo** de cada estrategia (o un **tope en $**
  equivalente) → solo dispara en un **desplome real**, sin costar neto en operación normal.

## 5. QualityScorer / régimen

- Probar `score_minimum` 55 y 60 por estrategia.
- **Activar solo si sube PF y neto**; si los recorta, dejar OFF.
- Régimen (p. ej. bloquear `trending_bear`) es opcional (útil en ES); probar caso por caso.

## 6. Decorrelación — elegir 2-3 por instrumento

- **Solape de señales** (misma barra 5m + mismo lado) **< ~30%**.
- **Correlación de P&L diario** (Pearson) **< ~0.3**.
- Objetivo: más **señales independientes** (compensa el recorte que dejan los filtros) y **curva de
  equity más suave**.

## 7. Reglas de cartera

- 2-3 por instrumento, decorrelacionadas.
- Vigilar **exposición agregada por símbolo** (varias del mismo instrumento netean en el bróker);
  cap por símbolo antes de ir a real.
- En demo: **un bot de TradersPost por estrategia** (datos limpios).

## 8. Proceso (pasos)

1. **ClaudeCode+TV:** 6-9 candidatas del instrumento (5m) → export trades+MAE/MFE, ATR(14), métricas,
   y matriz de solape/correlación.
2. Aplicar **§3** (descartar las que no califican).
3. **Calibrar:** SL catastrófico (§4) + QualityScorer si aplica (§5).
4. **A vs B** (estilo Anexo 22) para confirmar el valor de NTEXECG.
5. Elegir **2-3 decorrelacionadas** (§6).
6. **Alta** en NTEXECG (paper/demo) con webhooks; crear alertas en TradingView.
7. **Validar en demo ≥ 1 semana** antes de cualquier promoción a real.

## 9. Prompt reusable para ClaudeCode+TV (plantilla)

> Reemplaza `<INSTRUMENTO>` / `<MICRO>` / `<SÍMBOLO_TV>` y ejecuta.

```
Contexto: alimento un gateway externo (NTEXECG) que añade un SL catastrófico (anti-crash) y, donde
mejora, un filtro de calidad. NO quiero mejorar estrategias malas: busco estrategias YA buenas cuyo
único hueco sea el riesgo de cola. Trabaja en AUTOMÁTICO, sin pedir confirmación.

OBJETIVO: genera y backtestea 6 a 9 estrategias de <INSTRUMENTO> (<SÍMBOLO_TV>) en 5m, sobre el
máximo histórico, 1 contrato, sin comisiones ni slippage. Varía el modo de señal y los módulos para
obtener candidatas con SEÑALES POCO SOLAPADAS entre sí (favorece Confirmation + salidas de
seguimiento de tendencia tipo Smart Trail / Trend Catcher, que es lo que mejor combina con el SL/PF).

CRITERIO: prioriza las que tengan Profit Factor ≥ 1.8, win rate razonable y ≥ 60 operaciones.
Descarta de la entrega las que no cumplan.

POR CADA ESTRATEGIA ENTREGA:
1) CSV de operaciones en C:\NTEXECG\ClaudeCodeTV_<FECHA>\ListaDeOperaciones\
   Nombre: trades_<INSTRUMENTO>_<codigo-corto-ficha>.csv
   Columnas EXACTAS: trade_id, side(long/short), entry_time, exit_time, entry_price, exit_price,
   qty, pnl_usd, mae_usd, mfe_usd   (tiempos en ET; pnl/mae/mfe en USD del contrato grande, 1 lote).
2) Reporte .md: ficha técnica exacta; nº trades, PF, WR, Net, ganancia/pérdida promedio, PEOR
   operación, Max Drawdown ($ y %); $/punto grande y micro (<MICRO>); ATR(14) real 5m; periodo.
3) Una MATRIZ de coincidencia de señales (misma barra 5m + mismo lado) y de CORRELACIÓN de P&L
   diario entre las candidatas (para elegir 2-3 decorrelacionadas).

Usa las barras HOLC locales de C:\NTEXECG\NINJATRADER\HOLC (ya existen para ES, NQ, RTY, 6J).
Al final, tabla resumen: estrategia, PF, WR, peor operación, Max DD, % señales exclusivas, y rutas.
```

## 10. Instancia lista — NQ (próximo lote)

```
Contexto: alimento un gateway externo (NTEXECG) que añade un SL catastrófico (anti-crash) y, donde
mejora, un filtro de calidad. NO quiero mejorar estrategias malas: busco estrategias de NQ YA buenas
cuyo único hueco sea el riesgo de cola. Trabaja en AUTOMÁTICO, sin pedir confirmación.

OBJETIVO: genera y backtestea 6 a 9 estrategias de NQ (CME_MINI:NQ1!) en 5m, máximo histórico,
1 contrato, sin comisiones ni slippage. Varía modo de señal y módulos para que las SEÑALES SE
SOLAPEN LO MENOS POSIBLE entre sí (favorece Confirmation + Smart Trail / Trend Catcher).

CRITERIO: prioriza PF ≥ 1.8, WR razonable y ≥ 60 operaciones. Descarta las que no cumplan.

POR CADA ESTRATEGIA ENTREGA:
1) CSV en C:\NTEXECG\ClaudeCodeTV_NQ\ListaDeOperaciones\ con nombre
   trades_NQ_<codigo-corto-ficha>.csv y columnas EXACTAS:
   trade_id, side, entry_time, exit_time, entry_price, exit_price, qty, pnl_usd, mae_usd, mfe_usd
   (tiempos en ET; importes en USD del contrato grande NQ, 1 lote).
2) Reporte .md por estrategia: ficha exacta; nº trades, PF, WR, Net, gan/pérd promedio, PEOR
   operación, Max DD ($ y %); $/punto NQ ($20) y MNQ ($2); ATR(14) real 5m; periodo.
3) Matriz de coincidencia de señales y de correlación de P&L diario entre las candidatas.

Usa las barras HOLC locales NQ_5m/15m/1h de C:\NTEXECG\NINJATRADER\HOLC (ya existen).
Tabla resumen final: estrategia, PF, WR, peor operación, Max DD, % señales exclusivas, rutas.
```

> Con esos archivos: aplico §3-§6 (descartar / calibrar SL catastrófico / QualityScorer / A vs B /
> elegir 2-3 decorrelacionadas) y damos de alta las elegidas en demo.

---

## 11. Batería de pruebas por estrategia (qué se prueba y con qué script)

Herramienta única (corre 1–6 de un golpe, SOLO LECTURA):
`python -m scripts.eval_strategy_battery --trades <trades_*.csv> --sym <NQ|ES|GC|RTY|6J|...> --tf 5m`
Lee la lista de operaciones (con MAE/MFE) + barras HOLC locales; todo en USD/micro (2 micros).

| # | Prueba | Qué mide / para qué | Script(s) | Criterio de decisión |
|---|---|---|---|---|
| 1 | **Baseline nativo** | PF/WR/Net/DD/peor de la estrategia sin tocar | `eval_strategy_battery` (1); reportes ClaudeCode+TV | Filtro de entrada: **PF ≥ 1.8** |
| 2 | **Barrido SL k×ATR** | efecto de un SL operativo (2/2.5/3/4/8×) | `eval_strategy_battery` (2); `sim_sl_matrix.py`; `calibrate_sl_from_trades.py` | En "limpias" el SL ajustado **resta neto** → no usar tight |
| 3 | **SL catastrófico** | seguro de cola: SL en **p95 del MAE** | `eval_strategy_battery` (3) | Debe **cubrir el crash sin costar neto** |
| 4 | **Distribución MAE** | percentiles (p40/p50/p70/p95) para niveles y SL cat | `eval_strategy_battery` (usa percentiles) | Sitúa niveles de escalonado y el SL cat |
| 5 | **Compras escalonadas** | si entrar en pullback mejora (a igual tamaño) | `eval_strategy_battery` (4); `sim_scaled_entry.py`; `sim_sizing.py`; preview: `preview_scaled.py` | Activar **solo si supera** la entrada base |
| 6 | **QualityScorer** | si filtrar señales sube PF/neto (score 55/60/65) | `eval_strategy_battery` (5); `eval_quality_filters.py` | Activar **solo si mejora** PF y neto |
| 7 | **Filtro de régimen (HMM)** | si el régimen 1h filtra perdedores | `eval_strategy_battery` (6); `eval_quality_filters.py` | Opcional (ES: bloquear `trending_bear`) |
| 8 | **A vs B (NTEXECG vs nativo)** | valor neto y de riesgo del gateway completo | `compare_ntexecg_vs_luxalgo.py` | Confirmar que NTEXECG aporta |
| 9 | **Decorrelación / solape** | señales compartidas + correlación de P&L | matriz de ClaudeCode+TV (`senales_compartidas_*.csv`, `correlacion_pnl.csv`) | Elegir 2-3 con solape <30% y corr <0.3 |
| 10 | **Ventana / sesión** | RTH vs 24h vs AM | integrado en simulaciones / reportes | Mejor ventana por estrategia |
| 11 | **Validación en demo** | fills reales, bloqueos de filtro, dispatch | `show_strategy_configs.py`, `compare_filter_decisions.py`, `show_recent_deliveries.py` | ≥ 1 semana antes de promover |

## 12. Inventario de scripts (`scripts/`) y motores

**Pruebas / simulación (solo lectura):**
- `eval_strategy_battery.py` — **batería completa** (pruebas 1–6) sobre una estrategia.
- `compare_ntexecg_vs_luxalgo.py` — A (NTEXECG) vs B (LuxAlgo 2 micros).
- `eval_quality_filters.py` — QualityScorer + HMM (lift por filtro/umbral).
- `sim_sl_matrix.py` — matriz SL k×ATR · `sim_scaled_entry.py` — escalonado · `sim_sizing.py` — cantidades por nivel · `sweep_matrix.py` — barridos combinados.
- `calibrate_sl_from_trades.py`, `calibrate_all.py` — SL desde listas de trades.
- `preview_scaled.py` — previsualiza los legs escalonados que se enviarían.
- `train_hmm.py` — entrena el modelo HMM (opcional; baseline = Kaufman ER).

**Aplicación / configuración (dry-run + backup + auditoría):**
- `apply_strategy_calibration_v1.py` — escribe SL/atr_tf/ventanas por estrategia.
- `apply_scale_entry_design_v1.py` — siembra diseño escalonado · `set_scale_execution.py` — activa/desactiva ejecución escalonada.
- `apply_quality_filter.py` — activa QualityScorer · `apply_anexo21_demo.py` — GC score + YM régimen.
- `enable_traderspost_demo.py` — habilita dispatch a TradersPost demo · `sync_strategy_windows_v1.py` — ventanas.
- `rename_strategy.py` (--delete-old) · `delete_strategy.py` · `create_new_strategies_v1.py` — alta/gestión.

**Diagnóstico:**
- `show_strategy_configs.py` — config efectiva por estrategia + estado Anexo 21.
- `show_recent_deliveries.py` — envíos a TradersPost (valida legs escalonados).
- `compare_filter_decisions.py` — actividad de filtros en vivo (log de decisiones).
- `diag_profiles.py` · `import_results.py` (resultados TradersPost).

**Motores (en `app/services/`):**
- `quality_scorer.py` (Nivel 4 score) · `hmm_service.py` (régimen, Kaufman ER / HMM) · `filter_pipeline.py` (5 niveles) · `sl_tp_calculator.py` (SL/TP por ATR) · `session_validator.py` (ventanas) · `payload_builder.py` (incl. `build_scaled` escalonado) · `config_resolver.py` (jerarquía de config).

> Reproducibilidad: con `eval_strategy_battery.py` + `compare_ntexecg_vs_luxalgo.py` se reconstruye todo
> el análisis de una estrategia desde su `trades_*.csv` + HOLC, sin depender de memoria ni de pasos manuales.
