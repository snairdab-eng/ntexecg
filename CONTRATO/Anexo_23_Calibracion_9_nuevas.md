# Anexo 23 — Calibración y A vs B de las 9 estrategias nuevas (fase 1: SL)

**Fecha:** 2026-06-29 · **Datos:** ClaudeCodeTV_290626 (trades con MAE/MFE + ATR(14) real) · **Script:** scripts/cal9.py

## 1. Método (fase 1)
Por estrategia, sobre la lista de operaciones real (P&L, MAE, MFE por trade) + ATR(14) real por trade (HOLC 5m):
**A = NTEXECG** (Stop Loss k×ATR, **sin escalonado todavía**, 24h, 2 microcontratos) vs **B = LuxAlgo nativo, 2 micros**.
Barrido de SL k ∈ {2, 2.5, 3, 4, 8}×ATR; se reporta el k de mayor neto. (Sin TP, para no cortar los ganadores de las trend-following.)

> **Limitaciones:** (1) falta el **escalonado**, que es donde NTEXECG sacó ventaja en las 8 originales → esto es el *piso* del valor. (2) Modelo conservador: si MAE ≥ SL se asume stop (subestima el neto de A). (3) Sin comisiones/slippage. (4) Sin ventana (fase 2).

## 2. Resultados (USD/micro, A con su mejor SL vs B nativo)

| Estrategia | SL | PF A | Neto $ A | Peor $ A | PF B | Neto $ B | Peor $ B |
|---|---:|---:|---:|---:|---:|---:|---:|
| S7 NQ ConfAny·TC·HyperWave | 8× | 1.30 | 8,474 | −1,490 | 1.43 | 12,964 | −3,540 |
| S8 ES ConfAny·TC·WeakConf | 4× | **1.83** | 6,766 | **−431** | 1.73 | 7,438 | −1,485 |
| S5 ES ConfStrong·TSR·WeakConf | 8× | 1.20 | 1,917 | −1,002 | 1.44 | 3,850 | −1,222 |
| S2 ES ConfNormal·TC·TSR (=ES5m actual) | 8× | 1.13 | 1,509 | −1,243 | 1.71 | 6,508 | −2,032 |
| S1 RTY ConfStrong·SmartTrail·TrendCatcher | 8× | 1.21 | 835 | −670 | 1.97 | 2,825 | −1,120 |
| S3 6J ConfNormal·ContrarianUptrend·TrendTracer | 2.5× | 1.68 | 194 | −15 | 4.69 | 581 | −52 |
| S9 6J ConfNormal·MoneyFlow | 2.5× | 1.58 | 171 | −15 | 3.05 | 502 | −111 |
| S4 6J ConfNormal·TSR·MoneyFlow50 | 2× | **1.37** | 102 | −21 | 0.94 | **−42** | −534 |
| S6 ES ConfNormal·TC·HyperWave | 2.5× | 0.95 | −424 | −535 | 1.37 | 5,452 | −2,032 |

## 3. Lectura
1. **El nativo gana en neto en las 9.** El SL de NTEXECG aquí es **seguro de cola**, no generador de neto: estas estrategias ya vienen con cola controlada (peor trade nativo modesto). Distinto de las 8 originales, donde el nativo tenía colas catastróficas y el SL pagaba.
2. **Donde NTEXECG sí aporta:**
   - **S8** (ES ConfAny·TC·WeakConf, SL 4×): ~91% del neto nativo con **⅓ de la cola** y mejor PF. Mejor caso ajustado por riesgo.
   - **S4** (6J): el nativo es **perdedor** (PF 0.94, peor −534); NTEXECG lo **rescata** (PF 1.37, peor −21).
3. **El TP fijo 6×ATR no conviene** a las trend-following (corta los runners). Para estas: SL catastrófico amplio (8×) + sin TP.

## 4. Diversificación (del reporte de correlación de P&L)
- **Clúster correlacionado:** ES con Trend Catcher (S2/S6/S8), corr +0.20…+0.45 → **elegir una, no las tres**.
- **Mejores diversificadores:** **S5** (ES Strong, corr −0.18 con S6), **S3/S9** (6J), **S4** (señal casi independiente).
- **Sorpresa:** S1 (RTY)–S4 (6J) +0.40 (beta de mercado común) — tenerlo al dimensionar.

## 5. Recomendación
- **Avanzar a fase 2 (escalonado + ventana)** para las candidatas reales: **S8, S4** y, como diversificadores, **S5** y una de 6J (S3 o S9). Ahí se verá si el escalonado inclina el neto a favor de NTEXECG.
- **NQ S7** y las ES Trend-Catcher (S2/S6): el nativo domina hoy; sólo justifican NTEXECG si se valora el control de drawdown.
- Confirmar todo con la **semana en demo** (fills reales).

---

## 6. Fase 2 — escalonado + ventana (candidatas)

A = NTEXECG completo: entrada **escalonada** (2 micros repartidos) + **SL catastrófico** + **ventana**, sin TP. Se optimizó ventana × diseño × SL.

| Estrategia | A: ventana · diseño · SL | PF A | Neto $ A | Peor $ A | PF B | Neto $ B | Peor $ B | Veredicto |
|---|---|---:|---:|---:|---:|---:|---:|:--:|
| S8 ES ConfAny·TC·WeakConf | 24h · base2 · 4× | 1.83 | 6,766 | −431 | 1.73 | 7,438 | −1,485 | ≈ (mismo neto, ⅓ cola) |
| S5 ES ConfStrong·TSR·WeakConf | 24h · 1m+1@p50 · 8× | 1.31 | 2,194 | −733 | 1.44 | 3,850 | −1,222 | ↓ |
| S3 6J ContrarianUptrend | 24h · base2 · 2.5× | 1.68 | 194 | −15 | 4.69 | 581 | −52 | ↓ |
| S4 6J TSR·MoneyFlow50 | 24h · 0,1,1@p40,p70 · 2.5× | **9.95** | 260 | −4 | 0.94 | **−42** | −534 | **✅** |
| S9 6J MoneyFlow | 24h · base2 · 2.5× | 1.58 | 171 | −15 | 3.05 | 502 | −111 | ↓ |

El escalonado **no superó** a la entrada simple (base2) salvo en S4. En las demás, las entradas en pullback a igual tamaño no compensan los trades que se saltan (no retroceden) ni superan la salida nativa.

## 7. Conclusión estratégica (importante)

**NTEXECG aporta más valor a estrategias con buen edge pero COLA fea (o nativo perdedor); aporta poco a estrategias ya bien gestionadas.**

- Las 8 originales tenían colas catastróficas (p. ej. NQ nativo peor −$6,345) → el SL/escalonado de NTEXECG las mejoraba en neto y riesgo.
- Estas 9 las **optimizó la IA de LuxAlgo para stats limpias** (WR/PF altos, colas modestas) → NTEXECG les recorta cola pero **cuesta neto**. Paradójicamente, "demasiado limpias" deja poco margen a NTEXECG.
- Excepciones reales: **S4** (NTEXECG convierte un nativo perdedor en ganador) y **S8** (mismo neto, ⅓ de la cola).

**Implicación para sourcing:** para encontrar estrategias que *rindan más* con NTEXECG, no busques las de mejores stats nativas, sino **edge bruto fuerte con peor-trade / drawdown GRANDE** (cola fea). NTEXECG convierte ese edge-con-cola en edge suave. Pídele a LuxAlgo/ClaudeCode alta ganancia bruta y WR decente **sin** gestión de riesgo pre-optimizada (deja que el riesgo lo ponga NTEXECG).

## 8. Recomendación
- **A demo:** **S4** (rescate real; diversificador) y **S8** (control de cola). Tamaño chico, observar.
- **Las demás:** correrlas **nativas** o con NTEXECG solo como SL catastrófico de seguridad (8×), sin esperar mejora de neto.
- **Próximo lote de búsqueda:** estrategias de cola fea (alto bruto, peor-trade grande), que es donde NTEXECG paga.

---

## 9. Filtros de Nivel 4 — QualityScorer + HMM (la pieza que faltaba)

Aplicando el **QualityScorer** (composite de 4 subscores, funciones reales) y el **gate de régimen HMM (1h)** sobre las señales nativas (lift = quitar señales de bajo score / régimen). P&L a 1 contrato grande.

**QualityScorer (mejor umbral):**

| Estrategia | Baseline net / PF | Con score | Δ net | PF |
|---|---|---|---:|---:|
| **S6 ES·TC·HyperWave** | 27,262 / 1.37 | score≥60 | **+15,675 (+57%)** | **2.93** |
| **S5 ES Strong** | 19,250 / 1.44 | score≥60 | **+7,438 (+39%)** | **2.22** |
| S2 ES (=actual) | 32,538 / 1.71 | score≥55 | +1,175 | **3.18** |
| S7 NQ · S8 ES ConfAny · S1 RTY · S3/S4/S9 6J | — | — | **negativo** | filtro OFF |

**HMM régimen:** "solo ranging" resta en casi todas. Pero los perdedores de las **ES se concentran en `trending_bear`** → bloquear ese régimen (permitir `ranging` + `trending_bull`) suma: **S6 +$7,825 · S5 +$4,613 · S8 +$812**. En 6J/NQ/RTY el régimen no aporta.

### Conclusión actualizada
- **Donde NTEXECG sube el NETO (no solo recorta cola): las ES vía QualityScorer.** **S6** y **S5** son las grandes ganadoras (+57% y +39%); **S2** gana mucho PF. El filtro **quita las señales malas** que el nativo igual tomaba.
- El **régimen** complementa a las ES bloqueando `trending_bear`.
- **NQ, 6J, RTY**: ni score ni régimen ayudan (consistente con Anexo 21: NQ es contrarian, los filtros le restan).

### Recomendación final (las 9)
| Estrategia | Palanca NTEXECG que aporta | Acción |
|---|---|---|
| **S6 ES·TC·HyperWave** | **QualityScorer score≥60** (+ bloquear bear) | **Demo prioritaria** |
| **S5 ES Strong** | **QualityScorer score≥60** (+ bloquear bear) | **Demo prioritaria** |
| **S2 ES (=actual)** | QualityScorer score≥55 (sube PF) | Aplicar filtro a la ES en vivo |
| **S4 6J TSR·MF50** | SL 2.5× + escalonado (rescata nativo perdedor) | Demo (tamaño chico) |
| **S8 ES ConfAny** | SL 4× (⅓ de la cola, mismo neto) | Demo si valoras drawdown |
| S1, S3, S7, S9 | ninguna mejora clara | nativo / SL catastrófico de seguridad |
