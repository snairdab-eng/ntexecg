# Hoja de correcciones — TradersPost / LuxAlgo
### Suscripción "NT_Paper Micro 6J5m - Contrarian Any - Trend Tracer - Money Flow Above 50"
**Fecha:** 19 de junio de 2026 · **Base de comparación:** Manual interno NTEXECG + tabla de riesgo

> Nota: estos cambios se aplican manualmente en tu cuenta de TradersPost y en el wizard de la alerta de LuxAlgo. Esta hoja indica el valor correcto, dónde cambiarlo y por qué.

---

## 1. Resumen de correcciones

| # | Hallazgo | Valor actual (mal) | Valor corregido | Severidad |
|---|----------|--------------------|-----------------|-----------|
| 1 | Ventana de trading 1 termina de noche | 09:30 a.m. → **11:30 p.m.** | 09:30 a.m. → **11:30 a.m.** | 🔴 Alta |
| 2 | Símbolo del Yen (micro vs estándar) | ticker `6J` (estándar, $6.25/tick) | ticker `M6J` (micro, $1.25/tick) + verificar en paper | 🔴 Alta |
| 3 | Exit breakeven offset | `1.5` | `0` (o vacío) | 🟡 Media |
| 4 | Reject entry if signal is older than | `1` | `2` | 🟡 Baja |
| 5 | Allowed tickers | `Any tickers` | `Only selected tickers` = `M6J` | 🟡 Baja |
| 6 | `quantity` en el JSON | Por confirmar | Siempre presente | ✅ Verificar |

---

## 2. Detalle de cada corrección

### 🔴 Corrección 1 — Ventana de trading 1 (lo más urgente)
- **Dónde:** TradersPost → Edit Subscription → sección **TRADING WINDOW** → primera ventana.
- **Cambiar:** la hora final de `11:30:00 p. m.` a **`11:30:00 a. m.`**
- **Dejar igual:** los días (L–V) y la segunda ventana (`01:30 p.m. → 03:45 p.m.`), que ya están correctos.
- **Por qué:** con la hora actual la mañana se extiende hasta las 23:30, lo que **elimina el bloqueo de lunch (11:30–13:30)**, permite entradas pasando el cutoff de 15:45 y absorbe la segunda ventana. Con `11:30 a.m.` recuperas la lógica del manual:

| Bloque | Hora NY | Uso |
|--------|---------|-----|
| Mañana | 09:30 – 11:30 a.m. | Entradas permitidas |
| Lunch | 11:30 – 13:30 | Entradas bloqueadas |
| Tarde | 13:30 – 15:45 | Entradas permitidas |
| Cierre | 16:00 | Cerrar posiciones |

---

### 🔴 Corrección 2 — Símbolo del Yen (riesgo 5×)
- **Dónde:** wizard de la alerta en **LuxAlgo** → campo `ticker` del JSON universal.
- **Problema:** en tus pasos quitas la "M" y envías `6J`. Pero según tu propia tabla de riesgo:
  - `6J` = Yen **estándar** → **$6.25/tick** (stop de 40 ticks = **$250** por contrato)
  - `M6J` / `MJY` = Yen **micro** → **$1.25/tick** (mismo stop = **$50** por contrato)
- **Cambiar:** usa el símbolo **micro**. En NinjaTrader el micro del Yen normalmente es **`M6J`** (no quites la "M").
- **Verificación obligatoria (no opcional):** antes de confiar en el símbolo, manda **una orden de prueba en paper** y confirma en el broker:
  1. Qué contrato exacto se abrió.
  2. Que el valor por tick sea **$1.25** (micro), no $6.25 (estándar).
  - Si tu broker resolviera el micro con otro símbolo, ajusta según lo que muestre el broker en esa prueba — pero el criterio es: **el contrato que se abre debe ser el micro de $1.25/tick.**

---

### 🟡 Corrección 3 — Exit breakeven offset
- **Dónde:** TradersPost → sección **EXIT** → campo *Exit breakeven offset*.
- **Cambiar:** de `1.5` a **`0`** (o dejarlo vacío).
- **Por qué:** el manual recomienda no usarlo por ahora para evitar movimientos de salida que no estén plenamente entendidos. Reactívalo solo cuando tengas claro su efecto.

---

### 🟡 Corrección 4 — Reject entry if signal is older than
- **Dónde:** TradersPost → sección **ENTRY** → *Reject entry if signal is older than*.
- **Cambiar:** de `1` a **`2`** minutos.
- **Por qué:** en 5m, 2 minutos dan margen a la latencia sin aceptar señales atrasadas. (La salida ya está correcta en 2.)

---

### 🟡 Corrección 5 — Allowed tickers
- **Dónde:** TradersPost → sección **DETAILS** → *Allowed tickers*.
- **Cambiar:** de `Any tickers` a **`Only selected tickers`** y escribir explícitamente **`M6J`** (o el símbolo micro confirmado en la prueba del punto 2).
- **Por qué:** es la red de seguridad que habría evitado el riesgo del símbolo. Si por error llega una señal con el contrato estándar, TradersPost la rechaza.

---

### ✅ Corrección 6 — Confirmar `quantity` en el JSON
- **Dónde:** wizard de la alerta en LuxAlgo → cuerpo del JSON universal.
- **Verificar:** que el payload **siempre** incluya `quantity`. Regla crítica del manual: si no llega, la señal puede rechazarse o usar un default no deseado.

**Plantilla corregida de referencia (Yen micro):**
```json
{
  "ticker": "M6J",
  "action": "buy",
  "sentiment": "long",
  "quantity": "1",
  "price": "{{close}}",
  "time": "{{timenow}}",
  "interval": "5"
}
```
> Ajusta `quantity` al número de contratos micro deseado. `action`/`sentiment` los genera la lógica de la estrategia (buy/long, sell/short, flat/exit).

---

## 3. Orden recomendado para aplicar

1. **Corrige la ventana de trading** (11:30 p.m. → a.m.) — efecto inmediato sobre cuándo entra el sistema.
2. **Cambia el ticker a `M6J`** en LuxAlgo y pon **Only selected tickers = `M6J`** en TradersPost.
3. **Manda una orden de prueba en paper** y confirma contrato + valor de tick ($1.25).
4. Ajusta **breakeven offset = 0** y **reject entry = 2**.
5. **Verifica el `quantity`** en el JSON.
6. Repite la prueba en paper: buy, sell, reversa y exit; confirma que las entradas fuera de ventana se ignoran y las salidas sí se procesan.

---

## 4. Lo que NO se toca (ya está correcto)
Auto Submit · Asset class Futures · Allow signal overrides ON · No notifications · Both Sides + side swapping ON + subtract exit OFF · timezone America/New York · exits/cancels outside ON · Position Size None (override ON, sin fractional/add) · Open Orders (cancel existing ON, waits 120/60/120) · Entry Market + Default TIF/price + extended hours OFF + TP/SL None · Exit Market + Default + reject old 2 · Retry: No retries · días L–V · ventana 2 (13:30–15:45).
