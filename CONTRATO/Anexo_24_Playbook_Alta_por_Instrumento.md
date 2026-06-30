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

1. **Ideación con LuxAlgo** (3 a la vez): pedir candidatas del perfil (§9). 2b. **Estudio con ClaudeCode+TV
   UNA a la vez** (§10): por cada estrategia configurada en el chart, export trades+MAE/MFE, ATR(14) y métricas;
   se acumulan en la misma carpeta. La matriz de solape/correlación la calculo yo desde los CSV acumulados.
2. Aplicar **§3** (descartar las que no califican).
3. **Calibrar:** SL catastrófico (§4) + QualityScorer si aplica (§5).
4. **A vs B** (estilo Anexo 22) para confirmar el valor de NTEXECG.
5. Elegir **2-3 decorrelacionadas** (§6).
6. **Alta** en NTEXECG (paper/demo) con webhooks; crear alertas en TradingView.
7. **Validar en demo ≥ 1 semana** antes de cualquier promoción a real.

## 9. Ideación con LuxAlgo (3 estrategias a la vez)

LuxAlgo solo entrega de 3 en 3 y no exporta CSV/MAE/ATR → se usa solo para **proponer fichas** del
perfil correcto; el estudio real lo hace ClaudeCode+TV (§10).

```
Dame 3 estrategias de futuros <INSTRUMENTO> en 5 minutos con este perfil:
- Entrada tipo CONFIRMATION (a favor de tendencia): Normal, Strong o Any.
- Salida / trailing de tendencia: SMART TRAIL o TREND CATCHER (que deje correr a los ganadores).
- Que las 3 sean DISTINTAS entre sí (distinto módulo / confluencia / filtro) para que sus señales
  se solapen lo menos posible — las quiero como cartera decorrelacionada.
- Prioriza PROFIT FACTOR alto (≥ 1.8) y win rate razonable, con al menos ~60 operaciones.
- NO optimices ni ajustes stop loss ni gestión de riesgo: el control de riesgo (stop catastrófico
  anti-desplome) lo añade un sistema externo. Quiero el edge en crudo, no estrategias "ya protegidas".
Por cada una: ficha técnica exacta (modo de señal, módulos/filtros, lógica long/short, salida) y
métricas (nº operaciones, PF, WR, Net, gan/pérd promedio, PEOR operación, Max Drawdown $ y %).
Al final, tabla comparativa de las 3.
```
Para más candidatas, repetir pidiendo que sean **diferentes a las anteriores**.

## 10. Estudio con ClaudeCode+TV (UNA estrategia a la vez)

Modo real: el usuario configura **una** estrategia en la pestaña/chart activo y ClaudeCode la analiza.
Se corre una vez por estrategia; todas caen en la misma carpeta y se acumulan. Plantilla
(reemplaza `<INSTRUMENTO>`/`<SÍMBOLO_TV>`/`<$pt>`/`<MICRO $pt>`; para NQ: NQ · CME_MINI:NQ1! · $20 · MNQ $2):

```
Hay UNA sola estrategia LuxAlgo configurada en la pestaña/chart activo de TradingView; analiza ESA
(no generes otras). Trabaja en AUTOMÁTICO, sin pedir confirmación.

Activo <INSTRUMENTO> (<SÍMBOLO_TV>), 5m, máximo histórico, 1 contrato, sin comisiones ni slippage.
Ficha de esta estrategia: <PEGA la ficha exacta, ej. "Confirmation Strong - Smart Trail - Trend Catcher">
→ úsala para nombrar los archivos.

ENTREGA 2 archivos en C:\NTEXECG\ClaudeCodeTV_<INSTRUMENTO>\ListaDeOperaciones\ (crea la carpeta si
no existe; NO borres lo que ya haya: acumulamos una estrategia por corrida):

1) trades_<INSTRUMENTO>_<codigo-corto-ficha>.csv  — columnas EXACTAS, una fila por operación cerrada:
   trade_id, side(long/short), entry_time, exit_time, entry_price, exit_price, qty, pnl_usd, mae_usd, mfe_usd
   (tiempos en ET; pnl/mae/mfe en USD del contrato grande, 1 lote; mae=desv. adversa, mfe=desv. favorable).

2) Reporte_<INSTRUMENTO>_<codigo-corto-ficha>.md  — ficha exacta; nº trades, PF, WR, Net, gan/pérd
   promedio, PEOR operación, Max Drawdown ($ y %); $/punto grande (<$pt>) y micro (<MICRO $pt>);
   ATR(14) real 5m; periodo cubierto.

Usa las barras HOLC locales <INSTRUMENTO>_5m / _1h de C:\NTEXECG\NINJATRADER\HOLC (ya existen para
ES, NQ, RTY, 6J; no las regeneres). Al terminar, dime las rutas generadas.
```
> Con 6-9 acumuladas en la carpeta, corro `eval_strategy_battery` a cada una + la correlación/solape
> entre todas, y elijo las 2-3 decorrelacionadas con mejor control de riesgo + PF.

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
