# Machote de registro de estrategia — NTEXECG
### Ficha que se llena (manualmente) al dar de alta cada estrategia

> **Flujo:** crear perfil en NTEXECG → NTEXECG genera la **URL única** (estado `disabled`) → pegar URL en la alerta de LuxAlgo → completar esta ficha → pasar a `enabled`.
> **Regla de oro:** el perfil permanece `disabled` (no reenvía a TradersPost) hasta que esté completo y validado.
> **Leyenda:** 🔒 = lo genera/gestiona NTEXECG · ✍️ = lo llenas tú manualmente · ✅ = se usa como guardarraíl de validación

---

## 1. Identidad
| Campo | Valor | Origen |
|-------|-------|--------|
| ID interno | `__________` | 🔒 autogenerado |
| Nombre corto | `__________` | ✍️ |
| Descripción completa | `__________` | ✍️ |
| **URL única (webhook de entrada)** | `https://ntexecg.../hook/__________` | 🔒 generada al crear |
| Token de la URL | `__________` | 🔒 aleatorio, largo (UUID/32 bytes) |
| Estado | `disabled` / `enabled` | 🔒 / ✍️ (activación manual) |
| Fecha de alta | `__________` | ✍️ |
| Responsable | `__________` | ✍️ |

---

## 2. Definición de la estrategia (LuxAlgo) y backtest
| Campo | Valor | Origen |
|-------|-------|--------|
| Toolkit / categoría | `Signals & Overlays` / `PAC` / `Oscillator Matrix` | ✍️ |
| Gatillo (trigger) | `__________` | ✍️ |
| Filtro 1 | `__________` | ✍️ |
| Filtro 2 | `__________` | ✍️ |
| Condición de salida | `__________` (builtin-exits / señal contraria / ninguna) | ✍️ |
| Periodo de backtest | `inicio __________ → fin __________` | ✍️ |
| # operaciones | `__________` | ✍️ |
| Winrate | `__________ %` | ✍️ |
| Profit Factor | `__________` | ✍️ |
| Beneficio neto | `$__________` | ✍️ |
| Max drawdown | `__________ %` | ✍️ |
| Periodicidad / frecuencia | `__________` (p. ej. ~1 señal/día) | ✍️ |
| Tamaño de orden usado | `__________` (unitario por defecto) | ✍️ |

---

## 3. Instrumento y temporalidad (guardarraíles)
| Campo | Valor | Origen |
|-------|-------|--------|
| Símbolo esperado | `__________` (p. ej. `M6J`) | ✍️ ✅ valida vs `ticker` del payload |
| Tipo de contrato | `micro` / `mini` / `estándar` | ✍️ |
| Valor por tick | `$__________` | ✍️ (para cálculo de riesgo) |
| Tick mínimo | `__________` | ✍️ |
| Temporalidad esperada | `__________` (p. ej. `5`) | ✍️ ✅ valida vs `interval` del payload |

> Si el payload llega con un símbolo o temporalidad distintos a lo declarado → **rechazo** (atrapa el "me equivoqué de chart / pegué el JSON equivocado").

---

## 4. Ventana(s) de operación (propias de ESTA estrategia)
| Campo | Valor | Origen |
|-------|-------|--------|
| Timezone | `America/New York` | ✍️ |
| Días activos | `L M X J V` (marcar) | ✍️ |
| Ventana de entrada 1 | `__:__ → __:__` | ✍️ |
| Ventana de entrada 2 (opcional) | `__:__ → __:__` | ✍️ |
| ¿Salidas permitidas siempre? | `Sí` / `No` | ✍️ (recomendado: Sí) |
| Cierre forzado EOD | `__:__` | ✍️ |

> Esta es la decisión que movimos a NTEXECG: cada estrategia define su propio horario (puede no coincidir con el de la estrategia B). TradersPost queda con un envelope amplio de respaldo, no con el gate fino.

---

## 5. Filtros de calidad
| Campo | Valor | Origen |
|-------|-------|--------|
| Rechazo por antigüedad — entrada | `____ seg` | ✍️ ✅ |
| Rechazo por antigüedad — salida | `____ seg` | ✍️ ✅ |
| Dedup (ignorar repetida en ventana) | `____ seg` | ✍️ ✅ |
| Confirmaciones adicionales | `__________` | ✍️ (espacio futuro) |

---

## 6. Riesgo
| Campo | Valor | Origen |
|-------|-------|--------|
| ¿Stop obligatorio? | `Sí` / `No` | ✍️ ✅ (recomendado: Sí) |
| Stop esperado (ticks) | `____` | ✍️ |
| riesgo_usd máx por operación | `$____` | ✍️ ✅ |
| riesgo_usd máx por cuenta/día | `$____` | ✍️ ✅ |
| Cantidad máxima de contratos | `____` | ✍️ ✅ |

> Validación: `cantidad × stop_ticks × valor_tick ≤ riesgo_usd máx`. Si lo excede → **rechazo**.

---

## 7. Ruteo de salida (hacia TradersPost)
| Campo | Valor | Origen |
|-------|-------|--------|
| Webhook destino TradersPost | `https://webhooks.traderspost.io/trading/webhook/__________` | ✍️ |
| Cuenta / broker objetivo | `__________` (p. ej. `PAPER_FUTURES`) | ✍️ |
| Notas de ruteo | `__________` | ✍️ |

---

## 8. Checklist antes de pasar a `enabled`
- [ ] URL única generada y pegada en la alerta de LuxAlgo.
- [ ] Símbolo esperado y temporalidad declarados (sección 3).
- [ ] Ventana(s) de operación definidas (sección 4).
- [ ] Stop obligatorio y límites de riesgo definidos (sección 6).
- [ ] Webhook destino de TradersPost configurado (sección 7).
- [ ] Prueba en paper: una señal válida pasa; una con símbolo/temporalidad/antigüedad incorrecta es rechazada.
- [ ] Estrategia marcada como `enabled`.

---

## Ejemplo lleno (referencia)

**1. Identidad** — Nombre: `6J5m Confirmation Strong – Contrarian Uptrend – MF>50` · Descripción: estrategia Signals & Overlays, reversión por confirmación · URL: `https://ntexecg.../hook/a1f9c2e7-...` 🔒 · Estado: `enabled`

**2. Definición/backtest** — Toolkit: Signals & Overlays · Gatillo: `Confirmation Strong Bearish` (long) / `Bullish` (short) · Filtro 1: `Contrarian Uptrend/Downtrend` · Filtro 2: `Money Flow Above/Below 50` · Salida: `Confirmation Builtin-Exits` · Backtest: `2026-02-13 → ~100 días` · # ops: 81 · Winrate: 91.35% · PF: 4.68 · Neto: $6,006.25 · Max DD: 6.83% · Frecuencia: ~1 señal/día · Tamaño: unitario

**3. Instrumento** — Símbolo esperado: `M6J` · Tipo: micro · Valor/tick: $1.25 · Tick mínimo: 0.000001 · Temporalidad: `5`

**4. Ventana** — `America/New York` · L–V · Entrada 1: `09:30 → 11:30` · Entrada 2: `13:30 → 15:45` · Salidas siempre: Sí · EOD: `16:00`

**5. Filtros** — Antigüedad entrada: 120 seg · Antigüedad salida: 120 seg · Dedup: 5 seg

**6. Riesgo** — Stop obligatorio: Sí · Stop: 40 ticks · riesgo/op: $50 (40 × $1.25 × 1) · riesgo/día: $150 · Máx contratos: 2

**7. Ruteo** — Webhook: `https://webhooks.traderspost.io/trading/webhook/...` · Broker: `PAPER_FUTURES`
