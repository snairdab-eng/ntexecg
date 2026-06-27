# Anexo 17 — Matriz de simulación de SLs por instrumento (decisión data-driven) · v1.0

**Fecha:** 2026-06-27
**Ámbito:** Simulación de 7 escenarios de SL por instrumento/ventana sobre los CSV autorizados +
ATR(14) real en el TF propio, para decidir `sl_atr_multiplier` por instrumento **desde datos**.
**Estado:** Simulación corrida. Decisión por instrumento abajo; faltan confirmar GC y CL.
**Script:** `scripts/sweep_matrix.py` (reproduce toda la matriz).

Escenarios: **Nativo** (sin SL operativo) · 2.0× · 2.5× · 3.0× · 4.0× · 6.0× · 8.0×ATR · TP 6×ATR
(regla conservadora Anexo 11). Métricas en **micro $** (lo que se opera). Config actual seed = 2.0×.
`Δnat` = net − Nativo · `Δact` = net − 2.0×.

> Cómo leer: el SL solo actúa cuando el precio corre k×ATR en contra **antes** de la salida nativa
> de LuxAlgo. Más ancho = se activa menos = conserva edge pero deja mayor cola. El SL es **seguro**,
> no herramienta de ganancia.

---

## Matriz por instrumento

### ES→MES [5m] · RTH 09:20–15:45 (n=45) — *manejo especial (Anexo 11 + escalonado Anexo 14)*
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Nativo | 1,586 | 1.91 | 84 | −1,016 | 1,016 | 0% | +0 | +1,344 |
| 2.0× | 242 | 1.14 | 44 | −155 | 434 | 56% | −1,344 | +0 |
| **2.5×** | **915** | **1.58** | 58 | −194 | 292 | 42% | −671 | +673 |
| 3.0× | 600 | 1.32 | 58 | −233 | 411 | 42% | −987 | +357 |
| 8.0× | 690 | 1.29 | 78 | −621 | 621 | 18% | −896 | +448 |

### NQ→MNQ [5m] · 24h (n=65)
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Nativo | 2,866 | 1.44 | 83 | −3,172 | 3,588 | 0% | +0 | +4,880 |
| 2.0× | −2,014 | 0.58 | 29 | −362 | 2,637 | 69% | −4,880 | +0 |
| 2.5× | −2,308 | 0.58 | 35 | −453 | 3,480 | 63% | −5,174 | −294 |
| 4.0× | −2,189 | 0.69 | 48 | −724 | 3,761 | 51% | −5,055 | −174 |
| 6.0× | −1,551 | 0.79 | 60 | −1,086 | 3,823 | 37% | −4,417 | +463 |
| **8.0×** | **287** | **1.05** | 66 | −481 | 2,126 | 25% | −2,579 | +2,301 |

→ Todo k≤6 **pierde**; solo 8× queda positivo y acota el peor trade −3,172→−481. Nativo da más net
pero con cola brutal. **Decisión: 8× (o nativo con tamaño mínimo).**

### YM→MYM [15m] · 24h (n=48)
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **Nativo** | **2,269** | **1.92** | 90 | −918 | 918 | 0% | +0 | +2,639 |
| 2.0× | −370 | 0.73 | 38 | −83 | 507 | 62% | −2,639 | +0 |
| 4.0× | 213 | 1.12 | 58 | −158 | 574 | 42% | −2,056 | +583 |
| 8.0× | 115 | 1.05 | 71 | −294 | 1,092 | 29% | −2,154 | +485 |

→ Nativo domina y la cola es modesta (−918). Cualquier stop cuesta ~90% del edge. **Decisión: 8×
solo-catástrofe (cumplir regla), confiar en salida nativa.**

### RTY→M2K [15m]
**RTH 09:30–15:45 (n=22)**
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **Nativo** | **1,708** | **6.90** | 86 | −144 | 144 | 0% | +0 | +892 |
| 2.0× | 816 | 2.63 | 59 | −78 | 186 | 41% | −892 | +0 |
| 6.0× | 996 | 2.48 | 82 | −234 | 234 | 18% | −712 | +179 |
| 8.0× | 771 | 1.86 | 82 | −312 | 312 | 18% | −937 | −45 |

**AM 09:30–12:00 (n=11):** Nativo $1,322 PF 24.0 peor −58 · 4.0× $1,018 PF 11.55.

→ Cola nativa diminuta (−144). Native domina. **Decisión: 8× solo-catástrofe; native manda.**

### GC→MGC [5m]  — *el único donde un stop moderado MEJORA en RTH*
**24h (n=107)**
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **Nativo** | **13,539** | 1.95 | 61 | −2,939 | 3,869 | 0% | +0 | +10,812 |
| 2.0× | 2,727 | 1.33 | 35 | −308 | 1,556 | 61% | −10,812 | +0 |
| 8.0× | 4,652 | 1.36 | 59 | −817 | 2,162 | 18% | −8,887 | +1,925 |

**RTH 09:30–15:45 (n=25)**
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Nativo | 2,535 | 1.57 | 60 | −1,547 | 2,391 | 0% | +0 | −30 |
| 2.0× | 2,565 | 2.65 | 48 | −209 | 429 | 48% | +30 | +0 |
| **2.5×** | **2,948** | **2.73** | 52 | −262 | 536 | 40% | +413 | +384 |

→ **Dos modos:** 24h nativo = max net ($13.5k) pero cola −2,939; RTH 2.5× = mejor riesgo/retorno
(PF 2.73, mejor que nativo RTH, cola −262). **Decisión pendiente: max $ vs control de cola.**

### CL→MCL [15m] · 24h (n=105) — *frágil (PF 1.34)*
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **Nativo** | **2,045** | 1.34 | 78 | −2,083 | 2,237 | 0% | +0 | +2,236 |
| 2.0× | −191 | 0.94 | 48 | −202 | 1,005 | 52% | −2,236 | +0 |
| 4.0× | 68 | 1.02 | 66 | −405 | 1,475 | 32% | −1,977 | +259 |
| 8.0× | −440 | 0.92 | 72 | −809 | 1,476 | 21% | −2,485 | −250 |

→ Nativo es lo único claramente positivo (PF 1.34, débil) pero cola −2,083; con stop apenas
breakeven. **Decisión pendiente: operar (4×, cola acotada) vs shadow/skip (es el más débil).**

### 6E→M6E [5m] · 24h (n=99) — *wider MEJORA*
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| Nativo | 366 | 1.44 | 85 | −188 | 299 | 0% | +0 | +229 |
| 2.0× | 138 | 1.31 | 45 | −39 | 113 | 55% | −229 | +0 |
| **8.0×** | **463** | **1.80** | 78 | −53 | 97 | 20% | +97 | +326 |

→ El stop ancho **mejora** net y PF y baja la cola. **Decisión: 8×.** (Net micro pequeño.)

### 6J→MJY [5m] · 24h (n=78)
| Esc | Net | PF | WR% | Peor | MaxDD | %stop | Δnat | Δact |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **Nativo** | **383** | **3.99** | 94 | −67 | 67 | 0% | +0 | +464 |
| 2.0× | −80 | 0.62 | 42 | −10 | 75 | 58% | −464 | +0 |
| 8.0× | 5 | 1.02 | 73 | −39 | 115 | 24% | −378 | +86 |

→ Nativo domina, cola diminuta (−67). **Decisión: 8× solo-catástrofe; native manda.** (Net pequeño.)

---

## Decisión por instrumento (desde la simulación)

| Instr | Ventana | `sl_atr_multiplier` | Carácter de la decisión |
|---|---|---|---|
| **ES** | RTH 09:20–15:45 | **2.5×** | Cerrado (Anexo 11) + escalonado (Anexo 14). Especial. |
| **NQ** | 24h | **8.0×** | Único k positivo; acota cola −3,172→−481. |
| **YM** | 24h | **8.0×** | Solo-catástrofe; nativo manda, cola modesta. |
| **RTY** | RTH / AM | **8.0×** | Solo-catástrofe; nativo domina (cola −144). |
| **GC** | ⏳ 24h vs RTH | **8.0× (24h)** o **2.5× (RTH)** | **Pendiente:** max $ vs riesgo/retorno. |
| **CL** | ⏳ 24h | **4.0×** o shadow | **Pendiente:** operar acotado vs no operar (frágil). |
| **6E** | 24h | **8.0×** | Mejora net+PF y baja cola. |
| **6J** | 24h | **8.0×** | Solo-catástrofe; nativo manda (cola −67). |

**Patrón confirmado por datos:** el 1.5×/2.0× actual es subóptimo en los 8. **8×ATR** es la elección
correcta para NQ/YM/RTY/6E/6J (único positivo en NQ; en el resto, seguro barato que respeta la
salida nativa). ES queda en 2.5×. **GC y CL requieren tu decisión** (abajo).

## Decisiones abiertas
1. **GC:** ¿24h con 8× (net ~$4.6k micro, cola −$817, captura más $) o RTH con 2.5× (net ~$2.9k,
   PF 2.73, cola −$262, mejor riesgo/retorno)? El 24h nativo da $13.5k pero con cola −$2,939.
2. **CL:** es el más débil (PF 1.34). ¿Operar con 4× (≈breakeven, cola −$405) o dejarlo en
   **shadow** hasta tener más evidencia / arreglar nada (los datos ya son válidos, solo es flojo)?

## Caveats
Backtest sin comisiones/slippage; ATR(14) Wilder en TF propio; muestras chicas en RTH de YM(9)/
NQ(18) y FX → dirección robusta, magnitudes aproximadas. Validar OOS antes de subir tamaño.
