# Anexo 20 — Resumen operativo final: de la optimización a política de NTEXECG · v1.0

**Fecha:** 2026-06-27
**Base:** Anexo 19 (sizing C1-C2-C3) + Anexos 16–18. Selección por **PF → MaxDD → peor trade →
Net**, balanceado para producción si el riesgo está controlado.
**Estado:** Propuesta de política. **No aplicado a perfiles.** Métricas en micro $.

> ⚠️ **Importante (alcance de implementación):** NTEXECG hoy envía **una entrada + bracket SL/TP**;
> NO coloca aún órdenes escalonadas (1 market + 2 límites con stop a nivel posición). Lo aplicable
> **de inmediato** es: ventana, `sl_atr_multiplier`, `atr_timeframe` y salida nativa. Las
> **cantidades escalonadas** (`scale_entry_*`) son un **objetivo de diseño** que requiere código
> nuevo (Anexo 14 §8; probablemente vía bridge NinjaTrader). Hasta entonces, los perfiles
> escalonados se operan como entrada simple con el SL/ventana indicados.

---

## 1. Tabla consolidada por activo

| Activo | Micro | Ventana | SL | Niveles ATR | Conserv C1-C2-C3 | Balanc C1-C2-C3 | Agresivo C1-C2-C3 | Recomendación final | Estado |
|---|---|---|---|---|---|---|---|---|---|
| ES | MES | RTH 09:20–15:45 | 2.5× | 0·0.75·1.25 | 0-0-1 | **0-1-4** | 0-10-0 | Balanceado 0-1-4 | **production** |
| NQ | MNQ | 24h | 8.0× | 0·4·5 | 0-0-1 | **0-2-2** | 0-10-0 | Balanceado 0-2-2 | **production** (vigilar) |
| YM | MYM | 24h | 8.0× | 0·1.5·2 | 0-0-1 | **0-0-4** | 0-0-10 | Balanceado 0-0-4 | **production** (vigilar DD) |
| RTY | M2K | AM 09:30–12:00 | 4.0× | 0·0.5·1.5 | 3-0-0 | **5-0-0** | 10-0-0 | Directo 3-0-0 | **shadow** (n=11) |
| 6E | M6E | RTH 09:30–15:45 | 2.0× | 0·0.5·0.75 | 3-0-0 | 4-0-0 | 10-0-0 | Directo 3-0-0 | **shadow** (net bajo) |
| 6J | MJY | 24h | 8.0× | 0·2·3 | 0-3-0 | 0-3-0 | 0-10-0 | 0-3-0 | **shadow** (retorno bajo) |
| GC | MGC | **RTH 09:30–15:45** | 2.5× | 0·0.5·0.75 | 0-0-1 | **0-0-3** | 0-0-10 | Balanceado 0-0-3 (RTH) | **production** |
| GC | MGC | 24h (v1) | 8.0× | 0·0.5·7 | 0-0-1 | 0-1-4 | 10-0-0 | (alternativa de cosecha) | **shadow** (cola −8,168) |
| GC | MGC | PM 12:00–15:45 (v2) | 2.5× | 0·1.25·1.5 | 0-0-1 | 0-0-5 | 0-10-0 | PF alto pero n=11 | **revisar** |
| CL | MCL | 24h | 8.0× | 0·0.5·2.5 | 0-0-3 | 0-0-4 | 0-0-10 | Conserv 0-0-3 | **shadow** (cola/PF débil) |

**Motivos** (resumen): ver §2.

---

## 2. Análisis por activo (criterio PF→MaxDD→peor→Net)

**ES/MES — production, balanceado 0-1-4.** PF 2.34, DD $891, peor −$524.
**No entra en la señal:** C1=0 → solo compra en pullbacks de **0.75× y 1.25×ATR** (1 micro a 0.75,
4 a 1.25). Ventaja: mejor precio promedio (estrategia de fade en rango). **Riesgo a entender:** si
el precio se va a favor sin retroceder, **no toma posición** (pierde runners). Para participación,
una variante con C1≥1 es razonable; el escalonado puro maximiza PF, no la cobertura de señales.

**NQ/MNQ — production (vigilar), balanceado 0-2-2.** PF 2.16, DD $1,789, peor −$842.
**Requiere pullback profundo:** entra 2 micros en **4×ATR** y 2 en **5×ATR**; C1=0. NQ corre mucho
en contra antes de recuperar, por eso los adds van tan lejos. Sin retroceso profundo no hay
posición. Su edge vive en 24h/overnight (RTH es negativo). Vigilar por ser el de peor Calmar nativo.

**YM/MYM — production (vigilar DD), balanceado 0-0-4.** PF 1.88, DD $3,292, peor −$883.
**Espera el tercer nivel:** C1=C2=0, 4 micros solo en **2×ATR**. Entra únicamente tras un pullback
de 2×ATR → baja participación y DD relativamente alto. PF estable (n=48). Aceptable para producción
con tamaño contenido; vigilar el drawdown.

**RTY/M2K — shadow, directo 3-0-0 (→5-0-0).** PF 14.30, DD $290–483, peor −$290.
**La señal inicial es suficientemente fuerte:** lo mejor es comprar **todo en 0×ATR** (los adds casi
no llenan porque RTY rara vez retrocede). Métricas excelentes **pero n=11** (AM) → muestra
insuficiente para producción. Validar con más histórico antes de promover.

**6E/M6E — shadow, directo 3-0-0.** PF 6.53, DD $45–60, peor −$45.
**La señal inicial es mejor que esperar pullback** (entrada directa en 0×). Riesgo controlado, pero
el **retorno absoluto es muy bajo** ($635–847 micro) y n=20 → shadow hasta confirmar que vale la
comisión.

**6J/MJY — shadow, 0-3-0.** PF 1.52, DD $320, peor −$87.
Entra 3 micros en **2×ATR** (no en señal). **Retorno absoluto bajo** (~$361 micro) y PF modesto.
No justifica producción todavía; shadow.

**GC/MGC — production en RTH; 24h y PM como alternativas.**
- **RTH 2.5× 0-0.5-0.75 → production, balanceado 0-0-3:** PF 5.70, DD $1,127, peor −$549, n=25.
  Mejor relación riesgo/retorno con muestra razonable. Entra 3 micros en **0.75×ATR**.
- **24h 8× (v1):** mayor Net absoluto pero **cola enorme** (agresivo peor −$8,168, DD $19,553) →
  **shadow** (cosecha de overnight con tamaño muy controlado, no para arranque).
- **PM 2.5× (v2):** PF 8.13 pero **n=11** → **revisar** (muestra chica).

**CL/MCL — shadow, conservador 0-0-3.** PF 1.41, DD $2,435, peor −$1,669.
El más débil (PF 1.41) y de **cola alta** (agresivo peor −$5,563). Entra 3 micros en **2.5×ATR**.
Mantener en **shadow** y validar en vivo antes de arriesgar capital.

---

## 3. Escalonado vs entrada directa
- **Conviene escalonado (entrar en pullbacks):** ES (0.75/1.25), NQ (4/5), YM (2), 6J (2),
  GC-RTH/PM (0.75 / 1.25-1.5), CL (2.5). Su edge mean-reversion mejora con mejor precio de entrada.
- **Conviene entrada directa (todo en la señal 0×):** **RTY y 6E** — rara vez retroceden, los adds
  casi no llenan; cargar la señal rinde más y con cola mínima.

---

## 4. Política inicial sugerida

### 4.1 Listos para production
- **ES/MES** (RTH 2.5×, 0-1-4), **NQ/MNQ** (24h 8×, 0-2-2, vigilar), **YM/MYM** (24h 8×, 0-0-4,
  vigilar DD), **GC/MGC** (RTH 2.5×, 0-0-3).
  *(Aplicable ya: ventana + SL + salida nativa. El escalonado de cantidades espera implementación.)*

### 4.2 A shadow (operan en sombra, validan en vivo)
- **RTY/M2K** (n=11), **6E/M6E** (retorno bajo), **6J/MJY** (retorno bajo), **CL/MCL** (cola/PF
  débil), **GC-24h** (cola alta, alternativa de cosecha).

### 4.3 Requieren más simulación
- **RTY** y **GC-PM** (muestras n=11). Todos los perfiles **agresivos** (validar **con comisiones/
  slippage** y **out-of-sample** — el sizing actual no los descuenta). CL: re-evaluar cola.

### 4.4 Parámetros a escribir por activo (propuesta — NO aplicar aún)

| Activo | session_config_json (ventana) | sl_atr_multiplier | atr_timeframe | scale_entry_levels | scale_entry_quantities | max_micro_contracts |
|---|---|---|---|---|---|---|
| MES | RTH 09:20–15:45, L-V | 2.5 | 5m | [0.75, 1.25] | [0, 1, 4] | 5 |
| MNQ | 24h 18:00–17:00, D-V | 8.0 | 5m | [4, 5] | [0, 2, 2] | 4 |
| MYM | 24h 18:00–17:00, D-V | 8.0 | 15m | [1.5, 2] | [0, 0, 4] | 4 |
| M2K | AM 09:30–12:00, L-V | 4.0 | 15m | [0.5, 1.5] | [3, 0, 0] | 3 |
| M6E | RTH 09:30–15:45, L-V | 2.0 | 5m | [0.5, 0.75] | [3, 0, 0] | 3 |
| MJY | 24h 18:00–17:00, D-V | 8.0 | 5m | [2, 3] | [0, 3, 0] | 3 |
| MGC | RTH 09:30–15:45, L-V | 2.5 | 5m | [0.5, 0.75] | [0, 0, 3] | 3 |
| MCL | 24h, L-V | 8.0 | 15m | [0.5, 2.5] | [0, 0, 3] | 3 |

> `scale_entry_levels`, `scale_entry_quantities` y `max_micro_contracts` son **campos nuevos** (no
> existen en el modelo actual). Implementarlos requiere: migración (columnas), lógica de órdenes
> escalonadas con stop a nivel posición y manejo de fills parciales (Anexo 14 §8). `session_config_json`,
> `sl_atr_multiplier` y `atr_timeframe` ya existen en `asset_profiles` y se pueden aplicar hoy.
> Para shadow, los mismos parámetros pero el activo arranca en estado `shadow` (observa, no ejecuta).

### 4.5 Antes de aplicar
1. Confirmar perfiles de producción (¿balanceado o arrancar en conservador y escalar?).
2. Re-simular **agresivos con comisiones/slippage + OOS**.
3. Decidir si ES/NQ/YM incluyen un contrato en la señal (C1≥1) para no perder runners.
4. Implementar (o no) el escalonado real; mientras tanto, operar entrada simple con SL/ventana.

## Caveats
Backtest sin comisiones/slippage; ATR(14) en TF propio; muestras chicas en RTY-AM (11), GC-PM (11),
6E (20), GC-RTH (25); PF/DD escalan ~lineal con el tamaño (por eso el "agresivo" tiende a un solo
nivel). Reproducible: `python -m scripts.sim_sizing --source holc`.
