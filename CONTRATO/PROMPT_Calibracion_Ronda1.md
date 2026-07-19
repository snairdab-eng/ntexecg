# PROMPT para Claude Code — Calibración Ronda 1 (aplicar hallazgos del Laboratorio)

> Pégalo en Claude Code. **Aplica al sistema demo** dos resultados del Laboratorio, y NADA más:
> (1) `cancel_after` por estrategia (valores de diseño del lab) y (2) el filtro de régimen de RTY
> como **experimento a vigilar**. Todo por **CLI auditado**: dry-run primero, backup, audit,
> reversible. **No hagas commit/push** (yo lo hago desde NTDEV). **No corras `--apply` tú** — solo
> preparas el CLI y el dry-run; el OPERADOR ejecuta en el server tras mi visto bueno.

---

## Rol y alcance (léelo — el alcance es ESTRICTO)
Eres ingeniero senior. Esta ronda aplica **solo** dos cosas al sistema demo, con el resto **sin
tocar**:
- ✅ **(1) `cancel_after` por estrategia** = `entry_reserve_timeout_seconds`, con los valores de
  **diseño del lab** (historia profunda), no los de la demo dispersa.
- ✅ **(2) RTY** → filtro de régimen `regime solo 1h·trend`, **como experimento** (activar + vigilar).
- ❌ **NO** aplicar filtros de calidad en ES/NQ/CL/YM/GC (sus supervivientes son ⚠/delgados).
- ❌ **6E/6J** → nativo, sin acción.
Invariantes: paper/demo; dry-run + backup + audit en todo; reversible; **una sola caducidad**
(`entry_reserve_timeout_seconds` = "Cancel entry after" de TradersPost = reserva de símbolo NX-28).

## Contexto de datos
El Laboratorio (camino A) ya produjo `REPORTES/LAB_RESUMEN_<fecha>.md` y
`REPORTES/lab_features_<SYM>.json` con, por instrumento y por nivel ×ATR, el `cancel_after`
sugerido (mismo estimador que `scripts/pullback_timing.suggest_cancel_after`:
`min(3600, p90·60 + 60)`). Esos son los **valores de diseño** a aplicar.

---

## ÍTEM 1 — `cancel_after` por estrategia (valores de diseño del lab)

**Qué:** para cada estrategia con piernas límite, fijar `entry_reserve_timeout_seconds` = el
`cancel_after` del lab correspondiente a **su pierna límite más profunda**.

**Lógica de mapeo (por estrategia):**
1. Resolver su **instrumento** (ES/NQ/RTY/GC/CL/6E/6J/YM) desde el símbolo/mapa.
2. Leer su config de piernas: `levels` (offsets ×ATR de las piernas 2..N). La **pierna más
   profunda** = `max(levels)`. Si la estrategia es market-only (sin piernas límite) → **no aplica**,
   dejar como está.
3. Redondear `max(levels)` **hacia arriba** al nivel de la grilla del lab (conservador: garantiza
   tiempo suficiente para el toque) y leer el `cancel_after` de esa celda `(instrumento, nivel)`.
4. Ese es el `entry_reserve_timeout_seconds` de la estrategia.

**Entregable:** un CLI auditado (extiende `scripts/pullback_timing.py` con un modo `--from-lab`, o
un script hermano) que:
- **dry-run (default):** imprime una tabla `estrategia | instrumento | pierna más profunda ×ATR |
  nivel lab usado | cancel_after (s) | valor actual` para TODAS las estrategias. **No escribe nada.**
- **`--apply`:** escribe `entry_reserve_timeout_seconds` vía el `apply_suggestion` existente (merge,
  no reemplazo; AuditService), con **backup** del config previo. Reversible.
- Al final (siempre) imprime la **lista para TradersPost**: `estrategia → Cancel entry after = N s`,
  con el recordatorio de que **se fija A MANO** (no hay API) y debe coincidir exactamente.
- Reusa `suggest_cancel_after`/`pctl` — sin segundo estimador (una sola caducidad).

## ÍTEM 2 — RTY: filtro de régimen `1h·trend` (experimento)

**Qué:** activar en las estrategias de **RTY** `allowed_regimes = [trending_bull, trending_bear]`
(régimen leído en **1h**), como el lab identificó (`regime solo 1h·trend`, único superviviente sin
⚠, n_out=16, kept ~40%). Semántica viva intacta: **régimen unknown falla abierto** (no bloquea).

**Entregable:** aplicarlo por el **CLI de config auditado** (extiende el que ya maneja perfiles/
config de estrategia, o `manage_profiles.py`, siguiendo el patrón dry-run + backup + audit):
- **dry-run:** muestra, por estrategia RTY, el `allowed_regimes` actual → propuesto.
- **`--apply`:** escribe `allowed_regimes` + `regime_timeframe=1h` (o la clave viva equivalente) en
  la config, con backup y audit. **Reversible** (es un experimento).
- **Nota de vigilancia** en la salida: se espera que en demo el régimen **bloquee ~60%** de las
  señales RTY (kept 40%); vigilar en Analytics el block reason de régimen y el desempeño de las RTY
  que sí pasan, contra el PF esperado del lab. Si sobre-bloquea o corta buenas, se revierte.

---

## Protocolo (estricto)
1. Prepara ambos CLIs (o extensiones) en NTDEV, con sus tests si añades lógica nueva (el mapeo de
   pierna más profunda → nivel lab merece un test de respuesta conocida).
2. Corre **solo el dry-run** de los dos y **pega las tablas** (cancel_after por estrategia + RTY
   antes/después). **NO corras `--apply`.**
3. **Detente** ahí con los mensajes de commit sugeridos. El OPERADOR: revisa conmigo el dry-run,
   pushea el CLI desde NTDEV, el server pullea, y **el OPERADOR corre el dry-run y luego `--apply`
   en el server** (contra la DB demo) — no tú.
4. Tras `--apply`: el operador replica los `cancel_after` **a mano** en TradersPost.

## Fuera de alcance
Cualquier filtro de calidad en otros instrumentos; tocar 6E/6J; un modo "shadow" del pipeline
(para este experimento basta activar+vigilar en paper). El botón "aplicar" desde el visor sigue
fuera de alcance.
