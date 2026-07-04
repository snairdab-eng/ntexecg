# Riesgo — ES/MES · Confirmation Normal - Trend Catcher - Trend Strength Ranging
### (Salida de REFERENCIA del Motor de Riesgo — objetivo de aceptación para Fable5)

Fuente: `LuxAlgo® Backtester (S&O) [3.3.3]` · CME_MINI:ES1! · export 2026-07-04 · 5m · 1 contrato · sin comisiones/slippage.
Rango: 2026-03-24 → 2026-07-03 · **120 trades** · ATR14 entradas 1.41–15.53 pts (mediana 5.15).
⚠ **Cobertura HOLC:** `ES_5m.csv` truncado al 2026-06-22 → los últimos 18 trades (#103–120) usaron **ATR estimado** (última barra ~5.24). Conclusiones estructurales intactas; cola con menor precisión. **Acción: actualizar HOLC hasta 2026-07-03.**

---

## 1. LÍNEA BASE (listado crudo · 1 contrato @ señal · scripted exit)
| Métrica | Valor |
|---|---:|
| **Total PnL** | **$28,175** |
| Trades | 120 |
| Operaciones rentables | 95 (**WinRate 79.2%**) |
| **Profit Factor** | **1.62** |
| Ganancia bruta / Pérdida bruta | $73,925 / $45,750 |
| **Max Drawdown** | **$11,750** |
| Peor trade | **−$10,162** (#96, short 12-jun) |
| PnL / DD | 2.40 |

*(Nota escala: 1 contrato mini ES = $50/pt = 10 microcontratos MES. Todas las configs de abajo despliegan el mismo tamaño = 10 MES, comparables 1:1 con esta base.)*

---

## 2. ANÁLISIS DE CONTROL DE RIESGO

**a) Suelo del SL (distribución MAE→ATR de las ganadoras):** mediana 1.99×ATR · media 3.66 · p90 8.76 · p95 13.75 · máx 26.18 (anomalía #6).
→ Un SL preserva el 95% de ganadoras solo a ~14×ATR. A 6×ATR ya corta el 21%. **SL duro como múltiplo de ATR = DESCARTADO (net-negativo en todo 6–16×ATR): mata las ganadoras que aguantan pullback, que son el edge.**

**b) Backstop catastrófico ($ fijo) — el airbag:** óptimo **$5,000/ES = $500/MES = 100 pts ≈ 19×ATR típico**. Solo toca 3 de 120 trades (mata la anomalía #6, recorta los desastres #87 y #96). Suma **+$3,200** net y **−38% Max DD**. Funciona porque en $ fijo se estrecha (en ×ATR) justo en régimen volátil (donde revientan) y es intocable en calma.

**c) Asimetría Long/Short — el hallazgo mayor:**
| Lado | n | Net | PF | WinRate | Peor |
|---|---:|---:|---:|---:|---:|
| **Long** | 63 | $24,450 | **2.60** | 83% | −8,438 |
| **Short** | 57 | $3,725 | **1.12** | 75% | −10,162 |
→ **El sistema es un motor de LARGOS.** Los cortos son casi break-even. 5 cortos perdedores corrieron ≥3×ATR a favor y aun así perdieron (give-back); de los largos, 0.

**d) Techo del TP (MFE):** las ganadoras dejaron **$18,900 en la mesa** (MFE − salida). TP corto = veneno (capa ganadores). **TP asimétrico:** largos quieren TP profundo (**5.5×ATR**, tienden), cortos TP corto (**~1.0–1.5×ATR**, revierten). El desastre #96 tuvo MFE solo 1.8×ATR → TP no lo salva → **TP y backstop son complementarios.**

**Descartados (no aportan):** SL duro ×ATR · filtro de sesión/hora (4 sesiones net-positivas; solo modular tamaño, RTH mejor, Asia/ON ruido) · time-stop (PF plano + HOLC insuficiente).

---

## 3. CONFIGURACIONES SUGERIDAS — mapa de calor (10 MES, vs línea base)
Ordenadas por score (PF + control de pérdidas). 🟩 mejor · 🟨 medio · 🟥 peor por columna.

| # | Config | Net $ | PF | Max DD | Pérd. bruta | Peor | WinRate |
|--:|---|---:|---:|---:|---:|---:|---:|
| 1 | **Balanceada + Backstop + TP(L5.5/S1.0)** | 🟩51,534 | 🟩5.80 | 🟩4,323 | 🟩10,734 | 🟩−4,064 | 🟩77% |
| 2 | Config A + Backstop + TP(L5.5/S1.0) | 🟩51,059 | 🟩8.19 | 🟩3,328 | 🟩7,098 | 🟩−3,328 | 🟥25% |
| 3 | Config A (6@6.5+4@7.0) + Backstop | 🟨35,052 | 🟨3.72 | 🟨5,104 | 🟨12,879 | 🟩−3,328 | 🟥22% |
| 4 | Balanceada + Backstop | 🟨33,889 | 🟥2.50 | 🟨5,622 | 🟥22,585 | 🟨−4,064 | 🟨70% |
| 5 | Solo Largos + Config A + Backstop | 🟥26,797 | 🟩4.53 | 🟩3,328 | 🟩7,581 | 🟩−3,328 | — |
| 6 | Solo Largos + Balanceada + Backstop | 🟥28,941 | 🟨3.89 | 🟩4,345 | 🟩10,017 | 🟨−4,064 | — |
| 7 | Señal + Backstop (sin escalera) | 🟨31,375 | 🟥1.74 | 🟨7,900 | 🟥42,150 | 🟨−5,000 | 🟩78% |
| — | LÍNEA BASE (señal) | 28,175 | 1.62 | 11,750 | 45,750 | −10,162 | 79% |

Definiciones de las piezas:
- **Escalera balanceada** = 1 MES en cada `[0.5,1,2,3,3.5,4.5,5,5.5,6,6.5]×ATR` (participación 85%).
- **Config A** = 6 MES @ 6.5×ATR + 4 MES @ 7.0×ATR (máx calidad, participación ~29%).
- **Backstop** = stop de precio a 100 pts / −$500 por MES.
- **TP(L5.5/S1.0)** = TP en 5.5×ATR para largos, 1.0×ATR para cortos.

---

## 4. ROBUSTEZ (walk-forward, out-of-sample)
| Config | PF in-sample | **PF fuera de muestra** | Veredicto |
|---|---:|---:|---|
| Escalera + Backstop | 3.5–3.95 | **3.5–3.9** | ✅ Validado (par profundo estable ambas mitades) |
| + TP asimétrico (stack completo) | 5.80 | **3.5–4.1** | ✅ TP-largo 5.5 robusto; TP-corto NO generaliza (0.5 vs 2.5) |

**El PF 5.80 es in-sample optimista. Número de confianza real ≈ PF 3.5–4.0.** El grueso del edge está en la escalera+backstop; el TP largo suma, el corto úsalo genérico (~1.0–1.5×ATR), sin fiarse del decimal.

---

## 5. RECOMENDACIÓN
- **Config operativa (equilibrio):** Escalera balanceada + Backstop 100 pts + TP(L5.5 / S~1.0–1.5). Net in-sample ~$51k; **confianza OOS PF ~3.7–4.0**, Max DD ~$4–5k.
- **Config máxima suavidad:** Solo largos + Config A + Backstop (PF 4.53, DD $3,328) — sacrifica ~24% de net por la curva más limpia.
- **Airbag imprescindible:** Backstop **$500/MES (100 pts)** — valida el óptimo también con la escalera puesta.
- **Gestión por lado:** motor largo. Cortos → TP corto (1.0×ATR) o tamaño reducido, no eliminarlos (aportan net).

---
*Referencia interna: memoria `ntexecg-es-ladder-backstop`. Este archivo es el objetivo de aceptación: el Motor de Riesgo, corrido sobre este listado ES, debe reproducir estos números y esta recomendación.*
