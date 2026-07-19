# HANDOFF — Contexto al 2026-07-18 (cierre de jornada)

> Arquitecto: Fable 5 (Cowork) · Implementador: Opus 4.8 (Claude Code) ·
> Operador: único humano. Protocolo §0 intacto (Opus no commitea; revisión
> del arquitecto por lote; suite verde por corrida — determinista desde
> FIX-FLAKE).

## Estado del sistema (todo desplegado y pusheado)

- **Suite: 1223 verdes / 7 skipped**, una corrida, determinista.
- **Auditoría E2E 07-15 CERRADA** (D-1..D-5) + FIX-D4-bis. Hilo de precisión
  FX completo: señal → registro → cable, todo Numeric(20,10) + tick.
- **LX-15 completo** (aplicar-lo-que-ves + C1 móvil, 3 bugfixes, guarda
  permanente de render HTML). **FILTROS-OFF aplicado** (N4 fuera de las 7;
  el proyecto de filtros vive pausado en el Lab).
- **FIX-FX-BACKSTOP**: conversión USD→pts única con rejilla del tick,
  fail-honest sub-tick, auditor `audita_palancas_fx` (solo 6J estaba
  envenenada — restaurada por el operador).
- **BUG-HONESTIDAD cerrado**: semáforo aplica LX-7 (fuente única con gate y
  fila OOS), Δ re-armado vs corte 1h, geometría stop-en-escalera visible
  (banner + gate ámbar + auditor `audita_semaforo`).
- **RA-1** (modo re-armado en el motor, informativo) y **RA-2a** (cancelAfter
  1-3600s en toda pierna límite) desplegados. **RA-2b sub-paso 1** (config
  scale_entry.rearm + E1/E2) commiteado.

## Hallazgos de la fase de pruebas (el ojo del operador)

1. 6J ambas cuentas qty=2 → era config (design_only); operador la pasó a
   execute como prueba. Perfiles verificados.
2. Backstop FX aplicado como 0.0 → FIX-FX-BACKSTOP (P1 cerrado el mismo día).
3. Semáforo 🟢 con OOS n/s → **4 de 7 semáforos mentían** (LX-7 ausente).
   Verdad honesta de flota: solo ES-ConfNormal tiene verde legítimo; 6J,
   ES-CS, GC, RTY = ⚪ sin veredicto (muestra chica).
4. GC stop-dentro-de-escalera (backstop 5.04×ATR < C3 7.21×ATR, huérfana
   R-RA6 25.6%) — DECISIÓN: se queda en paper como está (el motor lo modela;
   la demo revelará el manejo real del broker). **CONDICIÓN DURA: GC no se
   promociona sin resolver la geometría.**
5. Números de GC verificados A MANO contra el CSV crudo: 7/7 exactos
   (Crudo −$14,160 / PF 0.90 / WR 54.8% / N 42). La aritmética del motor es
   sana; los bugs eran de la capa de presentación.

## Pendientes de MAÑANA (en orden)

1. **Operador**: re-Calcular estudio en las 4 con semáforo mentiroso (6J,
   ES-CS, GC, RTY) → verificar ⚪; luego en RTY: Restablecer → Aplicar (gate
   ámbar honesto) para limpiar la huella de los 2 applies gateados en falso.
2. **Opus**: RA-2b sub-paso 2 (estado persistente en risk_plan_json["rearm"]
   + helpers fail-closed — prompt ya entregado en el chat del arquitecto).
   Después: sub-pasos 3 (inferencia de precio) → 4 (motor de reglas) → 5 (el
   RearmJob, LA revisión profunda) → 6 (audit+adversariales E2E).
3. **Pruebas pendientes del plan**: T2/T3 de ES (escalonada + exit cancela
   piernas — verificar cancelAfter en el payload post-RA-2a), próxima
   entrada de 6J escalonada (2 cuentas divergiendo + precios FX al tick).
4. **Operador (siguen abiertos)**: firewall a IPs Cloudflare (subió de peso
   con FIX-D1), force_flat_time con margen vs 17:00 ET, backups Postgres,
   {{interval}} en alertas LuxAlgo, rotar tokens de webhook tras la fase de
   pruebas (viajaron por chats).

## Principio ganado esta semana (para specs futuros)

"Todo número que aparece en dos lugares debe salir de la misma fuente" — las
tres cazas del operador (TZ, semáforo, geometría) fueron divergencias de
fuentes. Guardas permanentes ya existentes: render HTML estricto, auditor FX,
auditor semáforo, regresiones con los números exactos de cada hallazgo.
