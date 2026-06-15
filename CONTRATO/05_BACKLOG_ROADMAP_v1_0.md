# NTEXECG — Backlog MVP y Roadmap v1.0

---

## Principio de desarrollo

Cada fase produce algo funcional y operable. Desde la Fase 1 el sistema recibe señales reales, toma decisiones y las muestra en UI. Las cuentas paper de TradersPost son el campo de prueba de cada fase antes de activar cualquier cuenta real.

---

## Fase 1 — Gateway funcional con UI operativa y SL activo

**Objetivo:** Recibir señales reales de LuxAlgo, evaluarlas con el pipeline de 5 niveles (Nivel 4 en placeholder), calcular SL con datos reales de NinjaTrader Bridge, despachar en dry_run, y gestionar el ciclo de vida de estrategias desde UI.

**Duración estimada:** 3-4 semanas en NTDEV.

**Incluye:**

```text
Infraestructura
  FastAPI + PostgreSQL + Docker Compose
  docker-compose.yml (NTEXECG) con montaje /mnt/ntbridge
  docker-compose.dev.yml (NTDEV) con YfinanceProvider
  Variables de entorno por entorno

Base de datos
  Todos los modelos SQLAlchemy (doc 04 v1.0)
  Migración inicial Alembic
  Seed completo: symbol maps, asset profiles, global profile,
                 strategy template, market_data_status inicial

Webhook Receiver
  POST /webhooks/luxalgo/{strategy_id}?token={secret}
  Validación de token (hash SHA256+salt)
  RawSignal guardado siempre
  Background task para procesamiento

Normalización
  ticker_received = exactamente payload["ticker"]
  SymbolMapper: búsqueda directa "MJY" → "MJYU2025"
  sentiment=flat → action=exit
  Tipos casteados, timeframe normalizado

Pipeline de 5 niveles (fail-fast)
  Nivel 1: validación sistema + check bridge activo
  Nivel 2: horario por activo + noticias (básico)
  Nivel 3: riesgo básico
  Nivel 4: score placeholder = 100 (estructura lista)
  Nivel 5: SL obligatorio por ATR vía MarketDataService

MarketDataService
  NinjaTraderBridgeProvider (producción — NTEXECG)
  YfinanceProvider (desarrollo — NTDEV)
  TradovateAPIProvider / DatabentoProvider (stubs)
  HeartbeatMonitor (APScheduler cada 30 segundos)

Strategy Registry
  Auto-create candidate con notificación en dashboard
  Comportamiento correcto por cada estado

Config Resolver
  Herencia global → asset → strategy

TradersPost Dispatcher
  PayloadBuilder: stopLoss SIEMPRE en entradas
  Modo dry_run (por defecto, seguro)
  WebhookDelivery registrado

Position State
  PositionService con state_source="estimated"
  UI indica estado estimado explícitamente

Performance Tracker
  StrategyPerformance actualizada por cada decisión

Gestión de ciclo de vida (100% manual)
  Flujo de nueva estrategia empatado con TradersPost
  Clonar estrategia desde UI
  Strategy Templates
  Acciones en lote
  Cambios con confirmación y AuditLog

UI completa — todas las páginas del MVP
  Dashboard con bridge status, métricas, feed
  Estrategias: lista, perfil completo con tabs, clonar
  Señales: lista con filtros + detalle con desglose pipeline
  Posiciones con estado estimado
  Symbol Mapper (formato "MES", pine_script_config visible)
  Asset Profiles (horario por activo)
  Strategy Templates
  Settings (incluyendo configuración del bridge)
  Audit log
```

**Criterio de éxito de Fase 1:**

```text
✓ Webhook real de LuxAlgo llega y se guarda
✓ ticker_received almacenado exactamente como llegó
✓ "MJY" → "MJYU2025" correctamente
✓ "M6J" → BLOCK symbol_not_mapped (con mensaje claro)
✓ sentiment=flat → action=exit
✓ Pipeline ejecuta 5 niveles en orden correcto
✓ Señal fuera de horario MES → BLOCK en Nivel 2
✓ Señal MJY a las 02:00 ET → PASS (sesión 24h)
✓ NT inactivo + entrada → BLOCK market_data_not_active
✓ NT inactivo + salida → PERMIT con warning
✓ Toda entrada APPROVE tiene sl_price calculado
✓ sl_price visible en detalle de señal en UI
✓ stopLoss incluido en payload hacia TradersPost
✓ ATR no disponible → BLOCK atr_calculation_failed
✓ dry_run=true por defecto en producción
✓ Bridge status visible en dashboard
✓ Nueva estrategia: UI muestra ticker exacto para LuxAlgo
✓ Clonar estrategia funciona en < 1 minuto
✓ Todo cambio genera AuditLog
✓ pytest pasa sin errores en NTDEV
```

---

## Fase 2 — Dispatch real a TradersPost (paper)

**Objetivo:** Enviar señales aprobadas con SL a las cuentas paper de TradersPost.

```text
Activar traderspost_enabled=true desde UI
Configurar webhook URLs paper por estrategia
Despachar señales APPROVE con stopLoss a TradersPost paper
Verificar que stopLoss llega correctamente (validar en UI de TradersPost)
Manejar errores HTTP y reintentos
Alerta en dashboard si delivery falla
```

**Criterio de éxito:**

```text
✓ Señal APPROVE llega a cuenta paper CON stopLoss
✓ TradersPost acepta el payload (verificar en su UI)
✓ Exit signals se reintentan hasta confirmar entrega
✓ dry_run desactivable desde UI con confirmación
```

---

## Fase 3 — Position State y reversas

**Objetivo:** Mantener estado correcto y separar entradas, salidas y reversas.

```text
PositionState actualizado por confirmaciones de TradersPost
Role classifier completo basado en PositionState real
Reversa separada: exit ejecutado + nueva entrada evaluada
allow_reversal configurable por estrategia
UI de posiciones con acciones flatten/lock/unlock
Estado UNKNOWN bloquea entradas automáticamente
```

---

## Fase 4 — Exit Manager básico

**Objetivo:** Controlar salidas autónomas independientemente de señales externas.

```text
APScheduler: tareas periódicas (cada minuto)
forced_close_time: cerrar posiciones a hora configurada
max_holding_minutes: cerrar si supera tiempo máximo
allow_overnight=false: forzar cierre antes del fin de sesión
break_even_ticks: mover SL cuando precio avanza X ticks
Exit Manager tiene prioridad sobre señales de entrada
Toda salida autónoma genera StrategyDecision y AuditLog
```

**Decisión pendiente sobre TP:**
Después de 30+ días de paper trading, evaluar con datos reales si:
- Mantener TP via Builtin-Exits de LuxAlgo (recomendado inicialmente)
- Agregar TP fijo por ATR
- TP parcial + dejar correr el resto
La decisión se toma con datos, no antes.

---

## Fase 5 — Filtros técnicos Nivel 4 + migración de datos

**Objetivo:** Activar los filtros de calidad del Nivel 4 y migrar a proveedor de datos independiente.

```text
QualityScorer — implementación real:
  volume_relative: volumen actual vs promedio 20 periodos
  atr_normalized: ATR en rango normal (no extremo)
  vwap_position: confluencia institucional (pit session)
  time_of_day_quality: calidad por hora del día

Todos no redundantes con LuxAlgo:
  LuxAlgo evalúa: momentum, tendencia, fuerza, ranging
  NTEXECG Nivel 4: volumen, ATR contexto, VWAP, timing

Migración de datos (cuando haya ganancias):
  Evaluar si NinjaTrader Bridge ha causado problemas
  Implementar TradovateAPIProvider o DatabentoProvider
  Cambiar MARKET_DATA_PROVIDER en .env → reiniciar contenedor
  Sin cambios en FilterPipeline ni SLTPCalculator

SLTPCalculator mejorado con datos en tiempo real del nuevo proveedor
```

---

## Fase 6 — HMM Market Regime

**Objetivo:** Clasificar el régimen de mercado con HMM para filtrado adicional.

```text
Motor: hmmlearn (pip install hmmlearn)
Datos: OHLCV de DB propia (guardados desde Fase 5)
Entrenamiento: APScheduler semanal por símbolo
Estados: Trending Bull / Trending Bear / Choppy/Ranging

Feature engineering: returns, volatilidad, volumen normalizado
Modelo HMM 2-3 estados por símbolo
HMMService.get_regime(symbol) → string

Estrategias declaran allowed_regimes en pipeline_config_json
Régimen visible en dashboard por símbolo
HMMService reemplaza stub de Fase 1
```

---

## Fase 7 — Portfolio Risk y Signal Conflict Resolver

**Objetivo:** Administrar múltiples estrategias, activos correlacionados y conflictos.

```text
Signal Conflict Resolver:
  Mismo símbolo, dirección opuesta → resolver por score
  Empate → rechazar ambas
  Conflicto registrado en conflict_log

Portfolio Risk Engine:
  Max open positions total
  Max exposure por correlation_group (configurable en UI)
  Daily loss stop global
  Portfolio mode: normal / defensive / flatten_only / paused
  Modo defensive: reducir size, aumentar score_minimum
```

---

## Fase 8 — Dashboard avanzado y métricas

**Objetivo:** Dashboard completo para tomar decisiones informadas.

```text
Métricas por estrategia: win rate real, PF real, trades/día
Comparación BT vs Real (ya disponible desde Fase 1)
Razones de bloqueo más frecuentes
Score promedio y distribución

Visualizaciones: señales por hora, score distribution
Reportes y exportación: CSV desde UI
Alertas configurables: sin señales en X horas, tasa de bloqueo alta
Notificaciones (email/Telegram, configurable)
```

---

## Resumen del roadmap

```text
Fase 1  — Gateway + UI + SL + bridge de datos    (3-4 semanas)
Fase 2  — Dispatch real a paper con SL           (1 semana)
Fase 3  — Position State + reversas              (1-2 semanas)
Fase 4  — Exit Manager básico                    (1-2 semanas)
─────────────────────────────────────────────────────────────────
          Sistema completo en paper con SL activo
          Evaluar TP fijo vs Builtin-Exits con datos reales
─────────────────────────────────────────────────────────────────
Fase 5  — Filtros Nivel 4 + migrar datos         (2-3 semanas)
Fase 6  — HMM Market Regime                      (3-4 semanas)
Fase 7  — Portfolio Risk + Conflict Resolver     (2-3 semanas)
─────────────────────────────────────────────────────────────────
          Sistema listo para evaluar paso a live
─────────────────────────────────────────────────────────────────
Fase 8  — Dashboard avanzado + métricas          (2-3 semanas)
─────────────────────────────────────────────────────────────────
          Sistema completo
```

---

## Reglas de transición entre fases

**Toda fase requiere:**

```text
1. Todos los tests pasando en NTDEV (pytest sin errores)
2. Funcionalidad validada en paper de TradersPost
3. UI actualizada y funcional
4. Al menos 5 días de operación en paper sin anomalías
5. Documentación actualizada
```

**Transición a cualquier cuenta real requiere adicionalmente:**

```text
- Fases 1-4 completas
- SL activo confirmado en TradersPost (verificado visualmente en paper)
- Mínimo 30 días de datos en paper
- Comparación BT vs Real revisada por el operador
- Activación manual desde UI con confirmación explícita ("CONFIRMAR")
- El operador toma la decisión. NTEXECG no la automatiza.
- Proveedor de datos confiable activo (evaluar si migrar de NT Bridge)
```
