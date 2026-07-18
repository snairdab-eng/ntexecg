# LOTE FIX-D-EJECUCION — modelo honesto de pierna-más-profunda-que-stop

> 2026-07-18 · Base: AUDITORIA_Total_Luxy_FixtureOro_2026-07-18.md §B ·
> REVISIÓN PROFUNDA (motor). Configs vivas NO tocadas; la re-derivación es
> reporte en scratch — el operador decide re-Calcular/re-aplicar con gate.

## (1) El fix — 4 rutas, un solo contrato

Pierna llenada a `d·ATR > b_pts` en trade stoppeado sale **≈ AL FILL**
(pnl = 0 − gap, jamás positivo). Semántica idéntica al peor-caso del
Portafolio (`position_sizing.worst_case_loss` / `portfolio_guard`:
`max(0, sl − L)`).

| Ruta | Cambio |
|---|---|
| `mr_sims.ladder_outcome` | `pnl_pts = -(max(b_pts - d·atr, 0) + gap)` (antes `-(b_pts + gap - d·atr)`) |
| `mr_sims._eval_proteccion` | ídem con `stop_pts` (freno generalizado SL×ATR/backstop) |
| `mr_sims.deep_leg_stress` | ídem con `d_max` (el estrés ahora mide contribución honesta) |
| `mr_luxy.luxy_outcome` | usa el `motivo` del exit común: con `motivo == "stop"`, cada pierna vale `min(ex + d, 0)·ATR` (BE/TP/nativo intactos — ahí el exit sí puede quedar por encima del fill legítimamente) |

Paridad v1↔Luxy conservada (`reconcile_trade_vs_v1` verde). Tests del
fixture de oro convertidos: el xfail del contrato correcto es VERDE
(−$1,200 exacto en ambas rutas), el PIN del modelo viejo se retiró y entró
un caso nuevo con gap (la pierna profunda PAGA el gap del exit: −$1,450) y
el borde pierna-apenas-menos-honda (−$1,300).

## (2) Cuantificación post-fix: $0 fantasma en las 7

Verificado contra la FUNCIÓN real (`luxy_outcome` corregida vs modelo
honesto a mano, masters reales del server, scratch local):

6E (sin backstop, trivial) · 6J (0 stoppeados) · ES-ConfNormal (4 stoppeados)
· ES-CS (4) · GC (20) · NQ (14) · RTY (3) → **fantasma = $0.00 en todas**.

## (3) Re-derivación en scratch — antes / después

Palancas derivadas: **IDÉNTICAS en las 7** (el backstop se elige señal-sola
en `backstop_sweep`, la escalera sale de MFE/MAE, el lado de los nativos —
ninguna depende del modelo corregido; BE siguió None en todas). Lo que se
mueve es la VALORACIÓN (Crudo+/OOS) donde había piernas más allá del stop:

| Clave | Crudo+ net (PF) antes → después | OOS net (PF) antes → después | Semáforo | Cambio |
|---|---|---|---|---|
| ES_ConfNormal | 33,710 (1.69) → igual | 11,704 (1.87) → igual | 🟢 → 🟢 | ninguno (b 90 pts = 19×ATR, piernas 1.56/3.12×) |
| ES_CS | 7,030 (1.87) → igual | 1,485 (1.54) → igual | ⚪ → ⚪ | ninguno |
| RTY | 39,442 (16.84) → igual | 12,767 (n/s) → igual | ⚪ → ⚪ | ninguno (b 52×ATR) |
| 6J | 3,787 (7.99) → igual | 1,061 (125.65) → igual | ⚪ → ⚪ | ninguno (0 stoppeados) |
| 6E | −951 (0.93) → igual | −3,841 (0.44) → igual | 🔴 → 🔴 | ninguno (sin backstop derivable) |
| **GC** | 11,858 (1.84) → **9,690 (1.59)** | 4,845 (2.50) → **4,699 (2.39)** · DD 1,694→1,841 | ⚪ → ⚪ (pocos_perdedores) | −$2,168 en Crudo+; C3 7.21× sigue MÁS ALLÁ del stop 5.28×ATR — la geometría persiste, ahora valorada honesta |
| **NQ** | 10,542 (2.61) → **5,891 (1.56)** · WR 42→35 · DD 1,696→**3,849** | **+2,066 (2.22) → −2,061 (0.59)** · perdedores 2→4 | ⚪ → ⚪ (muestra_chica n=8) | **CONFIRMADO: el OOS honesto de NQ es NEGATIVO** — el ⚪ escondía un rojo de facto; NQ está fuera de la flota activa (FILTROS-OFF) y así debe seguir |

Notas para el operador:
- **GC**: la pregunta "¿sigue eligiendo SL 57 pts ambos-lados?" — el estudio
  del 07-18 (antes Y después del fix) elige **backstop $3,000 = 30 pts** y
  lado **"cortar → SOLO CORTOS"** (no ambos). No hay 57 pts en las palancas
  derivadas de esta fuente; si la ficha muestra 57 pts, viene de otro lado
  (¿slider/estudio previo?) — revisar contra `runs/luxy_2026-07-18.json`.
  El delta de GC en filas (−$2,168) es menor que el fantasma total de la
  auditoría (−$5,167) porque el corte "solo cortos" ya excluía de las filas
  a los largos stoppeados; la condición dura de promoción sigue vigente
  (C3 más allá del stop → gate ámbar de geometría).
- El fantasma de la auditoría era cota total por trade; los deltas de tabla
  son sobre las filas VISIBLES (con el corte de lado aplicado) — misma
  dirección, magnitud por superficie.
- Scratch con los estudios re-derivados: `_scratch_fixd/MotorRiesgo/*/runs/`
  `luxy_fixd.json` + `_scratch_fixd/rederive_report.json` (local, no
  versionado). Nada del server ni de configs vivas fue modificado.
- Acción sugerida (decisión del operador): re-Calcular GC y NQ en la pestaña
  tras el deploy del fix para que los estudios persistidos dejen de mostrar
  los números inflados; aplicar (si procede) pasa por el gate normal.

## (4) Suite

Golden + archivos afectados: 161 passed / 1 skipped tras el fix, cero
fallout (ningún test fijaba el modelo viejo fuera del PIN diseñado para
morir). **Suite completa: 1266 verdes / 7 skipped / 0 xfail (14m 04s, una
corrida)** — el xfail del contrato quedó convertido en verde permanente.
