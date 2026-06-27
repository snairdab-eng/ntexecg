# Anexo 16 — Calibración de los 8 instrumentos con ATR(14) real · v1.1

**Fecha:** 2026-06-27 (revalidado) · v1.0 2026-06-26
**Ámbito:** Barrido de SL por ATR replicado en los 8 instrumentos de la Fase 1, con ATR(14)
Wilder **real por barra en el timeframe propio de cada estrategia** (no proxy, no todo 5m).
**Estado:** Hallazgos revalidados sobre los trade-lists autorizados del 27-jun (cada uno de su
pestaña propia). Pendiente: corregir HOLC de CL; decidir el modelo de SL por estrategia.
**Script:** `scripts/calibrate_all.py` (TF por instrumento; auto-detecta $/pt; verifica escala).

---

## 0. Titular

**La configuración cerrada de ES (RTH + SL 2.5×ATR) NO transfiere al resto.** Cada instrumento
tiene su propio timeframe, su propia ventana rentable y su propia lógica de indicador. El SL fijo
obligatorio cuesta edge en 7 de 8 (la salida nativa por señal domina). El rol de NTEXECG por
instrumento: **elegir la ventana correcta + salida nativa + stop de emergencia ancho**, no un SL
operativo apretado.

Validación del método: ES reproduce el Anexo 11 (RTH nativo PF 1.83; mejor SL fijo k=2.5 → PF
1.64; ATR medio 5.0 pt ≈ el proxy 7 de entonces). Motor correcto.

---

## 1. Tabla maestra revalidada (ATR real, TF propio, exports 27-jun)

| Instr | TF | n | PF 24h | PF RTH | PF AM | PF Overnight | net OVN std $ |
|---|---|---|---|---|---|---|---|
| ES→MES | 5m | 122 | 1.72 | **1.83** | 1.21 | 1.65 | 17,812 |
| NQ→MNQ | 5m | 65 | 1.44 | **0.43** 🔴 | inf(6) | **3.82** | 54,930 |
| YM→MYM | 15m | 48 | 1.92 | **0.43** 🔴 | 0.41 | **3.85** | 30,540 |
| RTY→M2K | 15m | 112 | 2.15 | **6.90** 🟢 | **24.0** 🔥(11) | 1.77 | 27,540 |
| GC→MGC | 5m | 107 | 1.95 | 1.57 | 1.27 | **2.13** | 110,040 |
| CL→MCL | 15m | 105 | 1.34 | 0.74 | 1.09 | 1.73 | 26,640 |
| 6E→M6E | 5m | 99 | 1.44 | **1.84** | inf(9) | 1.35 | 2,444 |
| 6J→MJY | 5m | 78 | **3.99** | 3.65 | 1.13 | 4.11 | 2,919 |

Montos en **contrato estándar**; para el micro dividir entre 10. `inf(n)` = sin perdedores en
muestra n (no fiable). CL reconciliado en §4 (PF 1.34 confirmado; CC reportaba 2.08 por export distinto).

ATR(14) real medio en entradas: ES 5.0 · NQ 22 · YM 44(15m) · RTY 4.0(15m) · GC 6.0 ·
CL 0.18(15m) · 6E 0.0003 · 6J 1.6e-6. *(El proxy de NQ de la 1ª corrida, 86 pt, estaba inflado ~4×.)*

### Cambios respecto a v1.0 (los "ajustes")
- **6J: 78 trades** (antes 26 — el export estaba incompleto por warmup del OscMatrix). Ahora con
  muestra válida: nativo 24h PF 3.99, overnight 4.11. Sólido en calidad, net micro pequeño (~$382).
- **TF correcto por estrategia:** RTY/CL/YM son **15m** (antes evaluados en 5m). El ATR cambia
  (RTY 2.1→4.0; YM 27→44) pero las conclusiones de ventana se sostienen.
- ES 120→122 trades, CL 104→105 (artefacto de roll). Sin cambio de fondo.
- Las 8 pestañas verificadas en vivo contra sus reportes (config @long/@short, TF, estudios).

---

## 2. Hallazgos (revalidados)

1. **El SL de 1.5×ATR es el PEOR punto en los 8.** Sin excepción. El bracket obligatorio debe ser
   más holgado.
2. **La salida nativa por señal iguala o supera a cualquier SL fijo** en 7 de 8; cuando el gateway
   obliga a stop, **más ancho es mejor** (k 4–8). Excepción → §2.4.
3. **La ventana NO es universal:**
   - RTH/AM excelente: **RTY** (RTH 6.90; AM 24.0), **ES** (1.83), **6E** (1.84).
   - RTH destructiva: **NQ** (0.43), **YM** (0.43), **CL** (0.74). Su edge está en **overnight**.
   - Overnight es la mina: NQ +$54.9k, GC +$110k, YM +$30.5k, CL +$26.6k, RTY +$27.5k.
   - El **régimen del filtro NO predice la ventana** (RTY y CL ambos "trending"; RTY brilla en
     RTH, CL no).
4. **Cada pestaña usa indicador/lógica/TF/régimen distintos** → no se puede clonar config entre
   instrumentos (la contaminación ES→NQ de la 1ª corrida lo probó por la vía dura).
5. **El TP de 6×ATR casi nunca dispara** (0–6 hits) → prácticamente inerte.
6. **Excepción — GC con stop ayuda:** GC en **RTH k=2.5 → PF 2.73** (vs nativo 1.57) y **AM k=2.5
   → 2.59** (vs 1.27). Único instrumento donde un SL fijo agrega valor (cola adversa intradía
   acotable). Candidato a SL 2.5×ATR si se opera su sesión de día.

---

## 3. Configuración recomendada por instrumento (revalidada)

| Instr | TF | Ventana | SL | ¿Señal cruda basta? | QS/HMM |
|---|---|---|---|---|---|
| **ES** | 5m | RTH 09:20–15:45 | **2.5×ATR** | Sí | no (cerrado, Anexo 11) |
| **NQ** | 5m | **24h/overnight (NO RTH)** | emergencia ~8×ATR + nativa | No | **evaluar** (matar RTH) |
| **YM** | 15m | **24h/overnight (NO RTH)** | nativa + emergencia ancho | Sí | si se fuerza RTH |
| **RTY** | 15m | **RTH / AM 🔥** | nativa; k alto solo emergencia | Sí | no |
| **GC** | 5m | 24h/overnight (max net) | nativa; **k=2.5 si se opera RTH/AM** | Sí | evaluar (cola, trade −$15k) |
| **CL** | 15m | 24h/overnight (NO RTH) | — | Sí | si se fuerza RTH |
| **6E** | 5m | **RTH / AM** | nativa o k=2 (RTH k2→PF 6.5) | Sí | no |
| **6J** | 5m | 24h | nativa (WR ~93%) | Sí | innecesario |

> El SL obligatorio del contrato (regla #6) se cumple con un **stop de emergencia ancho**; la
> protección de salida real la da la señal nativa de LuxAlgo. El bracket TP (Anexo 10) se mantiene
> donde el broker lo exija.

### Priorización de cartera (sugerida, revisada tras reconciliar CL)
- **Núcleo:** RTY (Calmar ~6, RTH/AM), GC (mayor net micro ~$12.9k, pero cola alta → candidato a
  filtro), ES (cerrado, RTH).
- **Sólidos:** YM (24h, PF 1.92), NQ (24h + stop ancho; peor Calmar ~0.9, vigilar),
  **CL (24h, PF 1.34 — modesto; baja de "núcleo" tras la reconciliación)**.
- **Marginales por $ (no por calidad):** 6E (~$366) y 6J (WR ~93%, Calmar alto, pero ~$382).

---

## 4. Pendientes / datos a corregir

1. **CL — RECONCILIADO (27-jun).** Veredicto desde el CSV autorizado `f9857` (105 trades, columna
   `PyG netas USD`, una fila/trade, qty=1, $/pt=1000 verificado): **WR 78.1% · PF 1.34 · net
   $20,450 · exp $195 · avg win $981 · avg loss −$2,607**.
   - El PF 2.08 / $41,700 del reporte de CC **no reproduce** este CSV: el *avg win es idéntico
     ($981)* pero los *perdedores del archivo autorizado son mayores* (−$2,607 vs −$1,843 de CC) →
     CC midió un export distinto/anterior y **sobreestimó CL ~2×**. Cifra correcta = **PF 1.34**.
   - La "escala 18%" es el **offset de nivel del contrato continuo back-adjusted** de NinjaTrader
     (close ~76 vs traded ~63–95). **NO afecta el ATR** (range-based) ni el barrido (merge por
     hora, no por nivel). No requiere arreglo para calibrar; solo importaría si usáramos niveles
     absolutos de precio. → **CL no se arregla; se acepta tal cual. Baja a "sólido modesto".**
2. **GC / 6E / 6J — ATR real aplicado** (el reporte de CC los marcaba como proxy poco fiable):
   ya recalculados aquí con ATR(14) real; conclusiones confirmadas (GC excepción §2.4; 6E RTH/AM;
   6J nativa 24h).
3. **Muestras chicas** en RTH de YM (9) y NQ (18), AM de NQ (6)/6E (9)/RTY (11) → direccional, no
   fino. Validar con más histórico / OOS antes de codificar ventana o filtro.
4. **QualityScorer/HMM:** evaluar (point-in-time + OOS, Anexos 12–13) donde el SL deje edge que
   defender: NQ/YM (filtrar la tarde destructiva si se quisiera operar RTH) y GC (cola).
5. **Re-backtest con filtro de sesión** activo por instrumento en su ventana recomendada, para
   medir las métricas reales que vería el gateway.

---

## 5. Reproducir

```
python -m scripts.calibrate_all            # las 8, TF propio, tabla maestra
python -m scripts.calibrate_sl_from_trades \
  --trades "ListaDeOperaciones/<archivo>.csv" --bars "NINJATRADER/HOLC/<SIM>_<TF>.csv" \
  --point-value <auto/valor>
```

Caveats globales: backtest sin comisiones/slippage; ATR(14) Wilder en el TF de la estrategia;
ventana por hora de entrada (ET); el micro divide los $ entre 10 (no la lógica).
