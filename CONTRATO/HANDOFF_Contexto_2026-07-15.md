# HANDOFF — NTEXECG · 2026-07-15 (cierre de la etapa Fable 5 · v2)

> Sucede a HANDOFF_Contexto_2026-07-12.md (que sigue vigente para L1–L7b/SEC/LX-1..10).
> Este cubre 2026-07-13→15: unificación NinjaTrader, saga del roll, LX-11..14b,
> auditoría E2E del despacho y el SPEC de re-armado. Roles y protocolo: SIN CAMBIOS
> (arquitecto revisa por lotes — profunda en motor/seguridad/datos/despacho; Claude
> Code implementa; SOLO el operador commitea/deploya; smoke navegador en lotes JS).

## 1. Arquitectura de datos NUEVA (la más importante de la etapa)

- **NinjaTrader (NTHOLC) es la ÚNICA fuente de historia**: estrategia unificada
  `NINJATRADER/NTraderUnifiedBridge.cs` = bridge de ejecución + exportador HOLC.
  Al activarla (ritual diario): reescribe {SYM}_{5m,15m,1h,4h}.csv completos en
  C:\NTEXECGSystem\Bridge\out; durante la jornada appendea cada barra cerrada;
  re-export de sanación cada 4h (const). Feed vivo (bars_*.json rolling ~300,
  heartbeat_*.json, JSONs de ejecución) INTACTO — CSV=memoria, JSON=presente.
- **Transporte**: el server monta //10.10.40.12/bridge/out en /mnt/ntbridge
  (CIFS ro). NTEXECG lee vía symlink `~/ntexecg/NINJATRADER/HOLC → /mnt/ntbridge`
  (¡la env var HOLC_DIR del .env NO llega a os.environ — variable fantasma,
  deuda P2: promoverla a Settings o documentar el symlink como canónico!).
- **Merge policy de NinjaTrader = "No fusionar respaldo adaptado"**
  (UpdatedUnadjusted, global en Options→Datos de Mercado) — mismo contorno que
  TradingView <sym>1!. La política back-ajustada anterior desalineó los 7 masters
  por escalones de roll (gaps constantes por tramo: ES −245 ticks, NQ −1130…)
  que la consistencia RELATIVA de los estudios NO detectaba (lección: todo par
  de fuentes de precio exige chequeo de contención ABSOLUTA desde el día uno).
- Jubilados: ohlcv_bars/stitch/MarketBarsUpdater (CSV-only, retiro P3 de Opus
  07-13; purga de la tabla = deuda P2), audit_ohlcv_tz (legado).

## 2. Guardias de datos (desplegadas, suite 1059/7 skipped)

- **LX-12**: contención GLOBAL al integrar (entrada∈[low,high] de su barra;
  CONTENCION_MIN_PCT=80) → <80% ⇒ intrabar_no_confiable + estudio DEGRADADO.
- **LX-13**: contención POR TRADE (±1 barra tolerada) → outliers de frontera de
  roll excluidos con nombre (ES 3 · NQ 1 · GC 3 · RTY 5) — jamás envenenan
  percentiles. Exclusión en UN punto: from_trades.
- Tripwires previos vigentes: implausible (PF>50, participación<90% con C1 al
  mercado), flip de signo, mejora>3×, PF "n/s" con <3 perdedores, muestra chica.

## 3. Gate + flota (LX-11, LX-14, LX-14b)

- **LX-11**: el Aplicar recomputa el gate SERVER-SIDE: 🟢 limpio · 🟡/part<90%
  checkbox · 🔴/implausible/flip/intrabar_no_confiable → frase "APLICAR SIN
  ROBUSTEZ". Señales completas en AuditLog (_gate_lx11).
- **LX-14**: semáforo gris "sin veredicto" si n_oos<10 (gate lo trata ámbar) +
  CONCENTRADO de flota en la lista de Estrategias (digest runs/resumen_flota.json,
  orden por atención ⛔→🔴→⚪→🟢→—). LX-14b: el digest FUERA del glob del estudio
  + shape-check fail-honest en _luxy_latest (el 500 del 'r'>'2').

## 4. Estado de la flota (2026-07-15, post-datos-limpios)

- 🟢 aplicadas y sanas: **ES ConfNormal** (OOS PF 2.81, retiene 140%) y
  **RTY15m** (PF 2.06, part 100%).
- 🟡 **ES ConfStrong** aplicada (frágil 1.21, n=35, sobreajuste) — decisión abierta.
- 🔴 **NQ** (no generaliza, confirmado con datos limpios; config viva aún del
  estudio envenenado del 13) → PAUSAR pendiente. **6E** rojo + APLICADA 07-14
  (¿pasó el gate? PREGUNTA ABIERTA) + afectada por D-2 (redondeo FX) → PAUSAR.
- ⏸ **GC** (crudo perdedor, flip) y **6J** (artefacto 98%-WR; su verde PF 14.57
  post-limpieza se lee con escepticismo — el crudo es el problema, no los datos).
- Falta poblar el tablero: Calcular estudio en las 7 tras LX-14b.

## 5. Auditoría E2E del despacho (2026-07-15) — 4 fixes EN COLA con prompts listos

Doc: CONTRATO/AUDITORIA_Despacho_E2E_2026-07-15.md (+13 tests e2e). Sin P0.
- **FIX-D2 (P1, primero, revisión PROFUNDA)**: redondeo AL TICK del catálogo en
  payloads (round(x,6) rompe 6J tick 5e-7) + decimal fijo sin científica (D-5).
  HASTA ENTONCES: no operar FX (6E pausada).
- **FIX-D3 (P2, profunda)**: cancelación explícita de C2/C3 al cierre (hoy quedan
  VIVAS hasta su cancel_after → fill huérfano posible). Prerequisito de RA-2.
- **FIX-D1 (P1 seguridad)**: cuarentena con cota para RawSignal no autenticado.
- **FIX-D4 (P2, alembic)**: Numeric(18,6)→(20,10) en precios del registro.

## 6. SPEC de re-armado de piernas (CONTRATO/SPEC_Rearmado_Piernas_2026-07-15.md)

Problema: TradersPost cancela límites a ≤60min; piernas profundas necesitan más
(p90 152min). Solución firmada: re-armado por ciclo **SIN SOLAPE** (re-envío al
min 61-62, DESPUÉS de la muerte certera de la orden vieja — regla del operador:
jamás dos límites vivos al mismo precio = fill doble). Invariantes: pierna muere
con la posición madre; estado ilegible → no re-armar; cotas; AuditLog por ciclo.
Lotes: RA-1 (motor, modo fills por duración) → RA-2 (job de despacho, tras
FIX-D3) → RA-3 (UI, default OFF). Modelo honesto: estudio ya tiene columnas
con-corte/sin-corte; re-armado = corte por duración del trade.

## 7. Pendientes del OPERADOR

1. Pausar NQ y 6E; aclarar el "aplicada 07-14" de 6E (Audit → _gate_lx11).
2. Calcular estudio en las 7 (tablero) y decidir ES-ConfStrong.
3. Mandar FIX-D2 → D-3 → D-1 → D-4 (prompts en el chat de cierre 07-15) y
   luego RA-1..3 cuando decida.
4. Fase siguiente: OBSERVACIÓN EN DEMO (ExecutionResult vs estudio — el
   ritual semanal: lista nueva → integrar → estudio → semáforo → gate).
5. Sigue pendiente de días previos: {{interval}} en alertas LuxAlgo, firewall a
   IPs de Cloudflare, 2FA, backups Postgres, reglas Portafolio 2–8, SEC-2.

## 8. Lecciones nuevas (acumulan a las del handoff 07-12)

- Consistencia relativa ≠ alineación absoluta (el roll fue invisible a todos los
  tests de tolerancia; lo cazó la contención cruda).
- Variables de .env NO llegan a os.environ (pydantic no exporta) — configurar
  sin verificar efecto = variable fantasma.
- Un digest nuevo puede colisionar con globs viejos ('r' > '2').
- El operador caza bugs de producción antes de que existan (gate, anti-solape):
  escuchar SIEMPRE la observación operativa y convertirla en invariante.
