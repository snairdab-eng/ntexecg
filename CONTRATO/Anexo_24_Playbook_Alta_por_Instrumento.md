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
