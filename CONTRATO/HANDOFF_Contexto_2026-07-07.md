# HANDOFF — Contexto de trabajo NTEXECG · 2026-07-07

> Actualiza y reemplaza al HANDOFF del 2026-07-06. Léelo de arriba a abajo;
> "QUÉ SIGUE" es lo urgente. Los principios, arquitectura, roles, flujo de
> deploy e invariantes del handoff anterior siguen vigentes tal cual.

## 1. Hecho y DESPLEGADO desde el handoff anterior (todo en origin/main, suite verde)

1. **R-obs cerrado** (`b2d2703`): protección = espejo completo de 5 palancas,
   TP nominal SIEMPRE (p99 + fallback 15×ATR), copy, unidades del Symbol
   Mapper, tarjeta "Configuración a aplicar". Verificado punto por punto +
   suite completa (757 verdes; los tests pesados de ES validados por fases
   contra la corrida real: recrear bit-a-bit idéntico, identidad del master,
   aceptación end-to-end $28,175).

2. **Bug de producción: botones muertos en Riesgo** (`493b6d0`):
   - Causa: autoescape de Jinja → `job: &#39;done&#39;` → SyntaxError → el
     componente Alpine entero muerto EN CUANTO había un job en memoria (por
     eso "funcionó una vez"). Fix: `{{ job | tojson }}` + test de regresión
     (`test_script_valido_con_job_en_memoria`).
   - Bonus: el SRI de htmx en base.html tenía la cola corrupta → el navegador
     bloqueaba htmx en silencio (bridge-badge congelado). Hash real
     verificado contra el archivo servido y corregido.
   - REGLA APRENDIDA: variables Jinja dentro de `<script>` SIEMPRE con
     `| tojson` (o ids validados por `_KEY_RE`).

3. **Auditoría de coherencia Estrategias↔Riesgo** (disparó el lote Puente):
   - Las 4 recomendaciones validadas (ES 90pts, RTY 110, GC 20, NQ 375)
     estaban SIN aplicar; TP vivo 6×ATR por DEBAJO del p99 de cierres
     (estrangula el edge); NQ con escalera 4×/5×ATR y C1=0 (casi no puede
     abrir posición); ES_ConfStrong y 6J SIN TP (sin bracket completo).

4. **Lote "Puente Riesgo↔Estrategias"** (spec:
   `CONTRATO/SPEC_Puente_Riesgo_Estrategias_2026-07-06.md`) — implementado,
   772 tests verdes, desplegado y validado en producción:
   - **P1 visibilidad**: campos MR-5 en `/api/strategies/{id}/config`
     (override+effective); avisos "SL×ATR / TP×ATR ignorado" y
     `short_size_factor` en la ficha de estrategia; **badge de deriva**
     (aplicada / difiere / sin aplicar / sin viva) en ambas pestañas.
   - **P2 aplicar supervisado**: `GET /ui/riesgo/aplicar/preview` (diff
     vivo→recomendado) + `POST /ui/riesgo/aplicar` (merge con whitelist
     estricta + AuditLog `APPLY_RIESGO_RECO`). JAMÁS toca
     mode/dry_run/traderspost/status; scale_entry preserva su mode (NX-11 —
     aplicar nunca arma execute). Botón + modal en la tarjeta de activación.
   - **P3 promoción estudio→viva**: CTA "dar de alta en Estrategias" en
     estudio validado sin viva → alta prellenada (`?from_estudio=`, id
     bloqueado, micro sugerido) → nace paper+dry_run → encadena al modal de
     aplicar (`?aplicar=1`; el flash del token webhook sobrevive).
   - Decisión de diseño: NO se fusionan las pestañas (laboratorio vs sala de
     máquinas); identidad única con dos puertas (Riesgo crea, Estrategias
     promueve/adopta).
   - Tests: `tests/test_puente_riesgo.py` (15).

## 2. QUÉ SIGUE (en orden)

1. **Aplicar las 4 recomendaciones con el botón nuevo** (ES, RTY, GC, NQ):
   Riesgo → "⇩ Aplicar a la config viva…" → revisar diff → Confirmar.
   ⚠ OJO: ya existe un estudio recalculado del 2026-07-07 en ES — revisar su
   ficha antes de aplicar (el diff mostrará los números de ESE estudio).
   ⚠ `cancel_after` se fija A MANO en TradersPost (ES: 2760s; resto 3600s).
2. **Las dos sin bracket**: `ES5m_ConfStrong_TSR_WeakConf` y
   `6J5m_ConfNormal_TSR_MF50` operan sin TP → subir su listado y calcular su
   estudio (o TP nominal manual) antes de la demo.
3. **Smoke check** tras aplicar: badge "aplicada" en las 4, aviso "SL×ATR
   ignorado" en sus fichas, `/api/strategies/{id}/config` con los MR en
   effective, y una señal de prueba en paper para ver el bracket del L5.
4. **Activación demo** — el hito pendiente del plan original.
5. (Opcional, recortable) UI de edición para `short_size_factor` y armado de
   escalera desde la ficha (hoy: script `set_scale_execution.py`).

## 3. Operativa / entorno

- El sandbox del asistente tiene tope de 45s por comando y su montaje puede
  servir COLAS TRUNCADAS de archivos recién editados durante minutos: correr
  suites y verificar archivos editados se hace en NTDEV con Claude Code
  (patrón probado dos veces: prompt de verificación → "LISTO PARA COMMIT" →
  el operador commitea/pushea/deploya).
- En NTDEV usar `.venv\Scripts\python.exe` (el global no tiene deps). Si la
  suite se cuelga: `faulthandler_timeout=300` y relanzar (cuelgue flaky
  conocido).
- `tests/` es paquete (`tests/__init__.py`): imports entre tests van con
  `from tests.test_x import ...`.
- La sesión web dura 8h — si "los botones no responden", primero F12/console
  y revisar si la sesión expiró.
