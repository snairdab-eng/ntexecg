# HANDOFF — Contexto de trabajo NTEXECG · 2026-07-06

> Documento de traspaso para retomar el trabajo sin perder contexto (compaction / nueva sesión).
> Léelo de arriba a abajo; la sección **"Estado actual / qué sigue"** es lo más urgente.

---

## 1. Qué es NTEXECG (en una línea)

Gateway **fail-closed** de señales de trading entre **LuxAlgo/TradingView (webhooks)** y **TradersPost**.
Stack: FastAPI + Postgres + SQLAlchemy async + Jinja2/HTMX/Alpine.js. **Solo paper/demo — nunca real/live.**
Desplegado en `cadmin@ntexecg` (ntexecg.lipatolicucho.com). Código fuente en NTDEV Windows: `C:\NTEXECG`.
Repo: `github.com/snairdab-eng/ntexecg.git`, rama `main`.

## 2. El principio rector (el "por qué" de todo lo reciente)

**PIVOTE ESTRATÉGICO:** ya NO buscamos mejorar la señal (los filtros no dieron edge robusto). Las señales de
LuxAlgo **ya tienen edge**; el problema son los **trades perdedores catastróficos** que "se llevan la mitad de
la cuenta o más". **NTEXECG existe para REDUCIR ese riesgo, no para aumentar la ganancia.**

Decisiones firmes del operador:
- **NO** forzar sizing al 1% (rechazado): la estrategia es para **un contrato MINI, sin SL apretado**; se acepta
  la pérdida sin bloquear la mayoría de los trades, porque ahí está el edge.
- Palancas de riesgo: **backstop (stop catastrófico de $ fijo) + escalera + TP nominal + gestión por lado**.
- **TradersPost exige SL y TP en toda orden** — el TP debe ser un **bracket nominal ancho** (por encima de los
  cierres de LuxAlgo) para que **LuxAlgo haga el cierre** y el TP casi nunca se toque.
- `cancelAfter` de TradersPost tiene **máximo duro = 3600s (1h)** → escaleras profundas no llenan → someras.

## 3. Roles y flujo de trabajo (IMPORTANTE)

- **Asistente (Claude Opus) = ARQUITECTO.** Escribe los prompts para Fable, verifica cada mini-reporte contra el
  código real, y da los comandos de deploy. **El asistente NUNCA hace commit ni push.**
- **Fable (Claude Code) = IMPLEMENTADOR.** Disponible **solo hasta el 7 de julio**. No hace commit/push.
- **El operador (tú)** hace commit + push desde NTDEV; el server hace pull.
- Convención de prompts: los bloques marcados `📋 PROMPT PARA FABLE` son para pegar en Fable; el resto (`💬 Para ti`)
  es comentario del arquitecto.

**Flujo de deploy (siempre):**
```
# NTDEV (PowerShell) — usa git add -u para no arrastrar archivos sueltos
git add -u
git add <archivos nuevos intencionales>
git commit -m "..."
git push

# Server (cadmin@ntexecg)
git pull
find app -name '__pycache__' -exec rm -rf {} + 2>/dev/null; true
sudo systemctl restart ntexecg
```
Notas: en el server usar `.venv/bin/python` (no `python`/`py -3`). En PowerShell NO usar comentarios `::` (es
sintaxis cmd, revienta). El montaje del sandbox del asistente puede ir **rezagado** respecto a NTDEV — el estado
real de git es el de la máquina del operador.

## 4. Arquitectura relevante

- **Pipeline de 5 niveles (fail-fast):** L1 sistema (kill-switch por capas, pausa global, symbol map, cuarentena)
  → L2 temporal (ventana de sesión + staleness) → L3 riesgo (estado de posición, symbol_busy) → L4 calidad +
  régimen → **L5 SL/TP por ATR (SL obligatorio)**.
- **Invariante fail-closed:** entrada sin SL/TP válido → **BLOCK**; `passed=True ⇒ sl_price nunca None`. La
  **guarda P0** en `sl_tp_calculator.py` bloquea brackets inválidos (lado incorrecto, precio ≤ 0).
- **Motor de Riesgo (offline, reusa el core del Lab):** `integrar` → `calcular` (estudios) → `recrear` (determinismo
  bit-a-bit) → `estado`. **Reusa `app/services/lab_metrics.py` y `scripts/lab_analyze.py` — NO borrarlos.**
- **Dos estudios espejo** en la pestaña Riesgo:
  1. **Estudio validado (fuera de muestra / OOS):** parte los trades in/out, la config debe **superar la base Y
     sobrevivir el gate OOS**.
  2. **Protección de cuenta (in-sample):** **mismas palancas**, pero sobre **toda la muestra sin apartar OOS ni
     gate**; selección por **supervivencia > net**. Cuenta editable (default **$10,000**, `MotorRiesgo/cuenta.json`);
     "sobrevive" = peor trade ≤ 10% de la cuenta Y DD < 100%.
- **Catálogo de instrumentos:** `symbol_maps.tick_value` + `tick_size` (Anexo 08 #4) = **fuente única** de datos por
  activo; la estrategia los hereda por su instrumento (ya cableado en `config_resolver`). **No duplicar por estrategia.**

## 5. Lo ya hecho y DESPLEGADO (auditoría total, Fase A + P0→P3)

Todo commiteado, pusheado y en producción (origin/main = **72241fb**), suite verde:
- **P0** — guarda fail-closed de bracket inválido en L5 (bug de la "orden desnuda": backstop mal escalado daba
  `sl_price` negativo con `passed=True`). Commit `471b113`.
- **P1 / P1b** — comunicación honesta del Riesgo + robustez; 4ª palanca **gestión por lado** (cortar/reducir el lado
  net-negativo que sostiene la catástrofe). Commits `7ae4535`, `2845799`.
- **v2 Riesgo** — protección de cuenta editable + ficha espejo + duración + rename/delete. Commit `699671e`.
- **reorg ficha** — tarjetas KPI de los dos estudios adyacentes, heatmap al final colapsado en `<details>`. Commit `6b597e0`.
- **P2** — **Dashboard + Analítica unificados** en `/ui` con selector de rango `{hoy,7,14,30,90}`; `/ui/analytics` →
  301; `routes_analytics.py`/`analytics.html` borrados; partials HTMX intactos (`bridge-badge` load-bearing en
  `base.html:72`). Commit `6571dad`.
- **P3** — Templates deprecado NO destructivo (modelo `StrategyTemplate` y columna `template_id` CONSERVADOS) + 5 sims
  pre-motor borrados (`sim_sl_matrix`, `sim_scaled_entry`, `sim_sizing`, `sweep_matrix`, `calibrate_sl_from_trades`);
  6 one-shots era Anexo21/v1 archivados en `scripts/archivo/`. Commit `72241fb`.

**Lab UI se CONSERVA** (el operador canceló su retiro; se mejorará después).

## 6. ESTADO ACTUAL / QUÉ SIGUE  ⬅ lo urgente

### Lote en vuelo: **R-obs** (NO commiteado, NO pusheado, NO verificado por el arquitecto)

Origen: 3 observaciones del operador sobre la ficha de recomendación de 6E. Alcance implementado por Fable:

1. **Protección de cuenta = espejo COMPLETO del estudio validado.** `proteccion_study`/`proteccion_para_cuenta`
   (`scripts/mr_sims.py`) debe estudiar **las 5 palancas** — SL ×ATR, backstop $ fijo, **escalera** (niveles ATR +
   cantidad por nivel), TP nominal por lado, gestión por lado (largos sí/no, cortos sí/no) — y sacar **todas las
   métricas** (PF, WR%, expectancy%, maxDD, net total, peor trade, participación, n). **Única diferencia con el
   validado:** usa toda la muestra **sin split OOS ni gate**; selección por supervivencia > net. (Corrección clave
   del operador: NO es "1 mini a la señal por diseño" — sí lleva escalera.)
2. **TP nominal SIEMPRE** en ambos estudios, por encima del **p99 de cierres reales de LuxAlgo**; nunca "sin TP";
   nunca cae al TP ajustado k×ATR (si falta muestra → default ancho documentado).
3. **Copy** de la ficha (`routes_riesgo.py:201-229`) sin cambiar números: SL como freno catastrófico (que no diga
   "backstop" en el título y "sin backstop" al final), escalera con recomendación real, TP con el nominal real.
4. **Unidades por activo** desde el catálogo Symbol Mapper (`tick_value`/`tick_size`); FX (6E/6J) en **ticks × valor_tick**,
   no puntos; si falta dato → aviso "catálogo incompleto".
5. Tarjeta **"Configuración a aplicar"** por estrategia (SL/backstop, TP nominal por lado, niveles ATR + cantidad,
   bloquear largos/cortos, junto al JSON de activación).

**Dónde quedó Fable (último reporte):** tests dirigidos verdes — 19 UI verdes, `mr_sims` verde con datos reales de ES
(54 passed, recrear bit-a-bit incluido). **La suite completa corría en background**; falta su resultado final + diff +
commit sugerido.

### Próximos pasos exactos (en orden)
1. **Esperar el cierre de Fable:** suite completa en verde + diff + commit sugerido.
2. **El arquitecto verifica contra el código:** (a) `proteccion_study` estudia las 5 palancas sobre toda la muestra
   y saca el set completo de métricas (espejo real del validado, solo sin gate OOS); (b) TP nominal SIEMPRE presente
   en ambas recomendaciones (nunca "sin TP"), anclado al p99 de cierres de LuxAlgo; (c) unidades desde el catálogo
   (ticks/$ en FX); (d) fail-closed y guarda P0 intactos.
3. **Solo tras verificación OK:** commit + push desde NTDEV → pull/restart en server → smoke check.
4. **Después de R-obs:** **activación demo** (siguiente en el plan).

### Recordatorio de deadline
**Fable se va el 7 de julio.** R-obs es el último lote grande. Si aprieta el tiempo, lo intocable es **(1) + (2)**
(protección estudia todo + TP nominal siempre); copy/unidades/tarjeta son alto valor pero recortables.

## 7. Archivos clave

- `scripts/mr_sims.py` — motor de estudios: `proteccion_study`/`proteccion_para_cuenta`, `leg_filled` (corte
  cancel_after ≤3600s), `fills_cutoff_study`, `side_management`. Constantes `GAP_STRESS_PTS`, `CANCEL_AFTER_MAX_S=3600`.
- `scripts/nt_riesgo.py` — CLI del Motor (`integrar`/`calcular`/`recrear`/`estado`); reusa `lab_metrics`, `lab_analyze`.
- `scripts/mr_report.py` — reporte; `fmt_stop` (FX en ticks/$).
- `app/web/routes_riesgo.py` — pestaña Riesgo; constructor de la ficha (~líneas 201-229), anti-traversal, cuenta editable.
- `app/templates/riesgo.html` — dos estudios espejo + heatmap colapsado.
- `app/services/sl_tp_calculator.py` — L5; branch de backstop; **guarda P0** de bracket.
- `app/services/config_resolver.py` — extrae backstop_points, tp_nominal_long/short, short_size_factor, tick_value/size.
- `app/services/filter_pipeline.py` — pipeline de 5 niveles (régimen en L4).
- `app/models/symbol_map.py` — catálogo `tick_value`/`tick_size`.
- `CONTRATO/` — SPEC del Motor, referencia ES, prompts de auditoría, `Auditoria_FaseA_2026-07-06.md` (backlog P0→P3).
- PDF de referencia por activo: `riesgo_real_futuros_ntexecg_actualizado.pdf` (tick_value/tick_size/costo por contrato
  de los 18 instrumentos; regla FX = pensar en ticks, no puntos).

## 8. Invariantes que NO se rompen (rojo si se tocan)
- Fail-closed: entrada sin SL **y** TP válidos → BLOCK; `passed=True ⇒ sl_price` nunca None; guarda P0 intacta.
- Kill-switch por capas y `symbol_busy` intactos.
- **NO borrar** `app/services/lab_metrics.py` ni `scripts/lab_analyze.py` (el Motor de Riesgo los reusa).
- **Lab UI se conserva** (retiro cancelado).
- `ConflictLog` se conserva (NX-18 Fase C).
- Solo paper/demo — nunca real/live.
