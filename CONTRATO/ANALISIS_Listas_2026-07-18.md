# ANÁLISIS — Listas de operaciones 2026-07-18 (ritual semanal)

> Arquitecto: Fable 5. Fuente: 7 CSVs nativos LuxAlgo subidos por el operador
> (PnL nativo full-size, SIN backstop/escalera — es la señal cruda).
> "NUEVOS" = trades con entrada ≥ 2026-07-13 (el periodo demo desde la lista
> anterior). Verificación aritmética independiente, fuera de NTEXECG.

## Tabla de flota (nativo, muestra completa)

| Estrategia | Rango | N | Neto | WR | PF | Perded. | Peor | DD | Semana nueva |
|---|---|---|---|---|---|---|---|---|---|
| ES_ConfNormal | 03-04→07-17 | 157 | +$33,275 | 79.0% | 1.53 | 33 | −$10,162 | −$11,750 | +$3,162 (7/9) |
| RTY_ConfNormal | **2025-08**→07-17 | 120 | +$49,230 | 86.7% | 2.26 | 15 | −$7,415 | −$7,465 | +$2,290 (2/3) |
| 6J_ConfNormal | 03-06→07-17 | 78 | +$3,544 | 96.2% | 6.79 | **3** | −$406 | −$594 | +$212 (5/6) |
| ES_ConfStrong ⏸ | 03-09→07-15 | 46 | +$8,662 | 87.0% | 1.42 | 6 | −$11,362 | −$14,188 | +$1,238 (3/3) |
| GC_ContraNormal | 03-05→07-16 | 44 | −$8,880 | 56.8% | 0.94 | 19 | −$24,460 | −$74,460 | +$5,440 (2/2) |
| 6E_ConfStrong ⏸ | 03-05→07-17 | 122 | **−$425** | 77.9% | **0.97** | 27 | −$1,875 | −$5,356 | **−$2,425 (2/8)** |
| NQ_ConfAny ⏸ | 03-19→**07-07** | 26 | +$4,675 | 76.9% | 1.07 | 6 | −$27,620 | −$41,895 | **ninguno** |

## Hallazgos por estrategia

**1. 6E — la señal nativa YA es perdedora y en caída libre.** Neto total
−$425 (PF 0.97) — la descripción de junio presumía WR 88% / PF 2.25; en un
mes se evaporó. La semana nueva: 8 trades, WR 25%, −$2,425. El semáforo rojo
decía la verdad. VEREDICTO: sigue pausada; si la próxima lista repite,
candidata a retiro/cuarentena formal.

**2. NQ — muda 11 días y con colas letales.** Último trade 07-07. PF nativo
1.07 sostenido por pocos largos; cortos −$22,235; peor trade −$27,620.
VEREDICTO: pausada bien; candidata a retiro. "ConfAny" casi no genera señal
en el régimen actual.

**3. 6J — el artefacto se sostiene, pero es un MONOCULTIVO.** WR 96.2%,
PF 6.79... y **76 de 78 trades son CORTOS**. No es una estrategia: es una
posición estructural corta-yen en cámara lenta ($47/trade promedio). Un
rally del yen (intervención BOJ) se lleva meses de racha en días. Además:
con 3 perdedores totales, LX-7 la deja en ⚪ PERPETUO — su única evidencia
posible es la demo. VEREDICTO: tamaño mínimo permanente; la escalera en
execute (prueba del operador) vigilada; jamás promoción por WR.

**4. ES_ConfNormal — el caballo de batalla, confirmado.** +$33k nativos,
semana nueva sana (+$3,162, 7/9). Matices: asimetría de lado (L +$24,375 vs
S +$8,900) y colas de −$10k que son exactamente lo que el backstop de 90 pts
capa. El único verde legítimo de la flota lo respalda la nativa. VEREDICTO:
ancla de la flota; sin cambios.

**5. ES_ConfStrong (pausada) — expectancy positiva, cola brutal.** +$8,662
pero peor −$11,362 y cortos NEGATIVOS (−$1,050 en 21). Si algún día vuelve:
cortar cortos + backstop obligatorios, y con 6 perdedores va a ⚪ largo rato.

**6. GC — la débil, PERO el "cortar long" tiene base estructural.** Nativa
−$8,880 (PF 0.94) con la asimetría más brutal de la flota: **largos −$56,440
· cortos +$47,560**. El flip crudo→config que nos preocupaba no es pura
manufactura del optimizador: media estrategia (fade de rallies del oro)
funciona y la otra media destruye. Sigue siendo muestra chica (22/lado).
VEREDICTO: se mantiene la decisión de ayer — paper mínimo, geometría
stop-en-escalera observada, no promocionable sin resolverla.

**7. RTY — la estrella, con asterisco de lado.** +$49,230, PF 2.26, y la
mejor base estadística de la flota: 11 meses, ~9 trades/mes estables. El
asterisco: **TODO el edge es de largos** — L +$48,700 vs S +$530 (60 trades
cortos = peso muerto). El estudio Luxy dijo "Lado: ambos" (in-sample y OOS
coinciden); la nativa dice que los cortos no aportan desde hace 11 meses.
TEMA PARA DISCUTIR: palanca de lado en RTY (cortar cortos duplicaría el
$/trade sin tocar el neto) — pero vía estudio→gate→Aplicar, no a mano.

## Conclusión de flota

La lista nueva VALIDA los semáforos honestos de ayer: los rojos eran rojos
(6E, NQ), el único verde legítimo (ES-CN) tiene respaldo nativo, y los ⚪
son ⚪ de verdad (muestras sin sustancia). La demo + la nativa cuentan la
misma historia — el sistema y la realidad están alineados.

## Pasos del ritual (operador)

1. Integrar las 7 listas nuevas (nota: 6 archivos dicen "180826" y uno
   "180726" — typo del export, irrelevante; la clave la da el nombre).
2. Verificar contención LX-12/13 por estrategia (≥80%, outliers de roll).
3. Calcular estudio en las 7 → esto CIERRA de paso el re-cálculo pendiente
   de los 4 semáforos mentirosos (6J, ES-CS, GC, RTY → deben caer a ⚪).
4. RTY: Restablecer → Aplicar con gate honesto (limpia la huella de los 2
   applies gateados en falso).
5. Revisar deriva/gates; re-aplicar donde el estudio nuevo lo amerite.
6. Discusión pendiente con el arquitecto: lado corto de RTY · futuro de
   6E/NQ (retiro?) · 6J monocultivo.
