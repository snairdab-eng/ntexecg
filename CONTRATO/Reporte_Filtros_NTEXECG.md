# Reporte de filtros — NTEXECG

> Cómo funciona cada filtro **por separado** y **en conjunto**. Basado en el código real del
> pipeline (`app/services/filter_pipeline.py`) y en el estándar de calidad del Anexo 25.
> Generado 2026-07-04.

---

## 1. El marco: un pipeline fail-fast de 5 niveles

Cada señal de LuxAlgo entra a un **pipeline de 5 niveles** que se evalúa **en orden y se detiene en
el primer bloqueo** (fail-fast). Una señal se **APRUEBA** (y se despacha a TradersPost) **solo si
pasa los cinco**. Si cualquier nivel la bloquea, se registra el **nivel** y el **motivo**
(`block_level`, `block_reason`) y no se sigue evaluando.

El orden no es arbitrario — va de lo **barato y de sistema** a lo **caro y de mercado**:

| Nivel | Qué gobierna | Pregunta que responde |
|-------|--------------|-----------------------|
| **L1 · Sistema** | Estado global, kill-switch, símbolo, guardarraíles | ¿El sistema está habilitado para operar esto? |
| **L2 · Temporal** | Ventana de sesión, frescura de la señal | ¿Es un buen momento y la señal está vigente? |
| **L3 · Riesgo** | Estado de posición, una posición por símbolo | ¿Es seguro abrir dado lo que ya tengo? |
| **L4 · Calidad + Régimen** | QualityScorer, régimen de mercado | ¿La señal es de suficiente calidad y el mercado acompaña? |
| **L5 · SL/TP** | Stop-loss por ATR (obligatorio), take-profit | ¿Puedo calcular una protección válida? |

**Las salidas (exits) son distintas:** deben poder pasar siempre para cerrar posiciones, así que
están **exentas** de la mayoría de los gates (sobre todo del `symbol_busy` de L3); los cierres
forzados y las reversiones se despachan a todos los perfiles.

---

## 2. Los filtros, por separado

### L1 — Sistema (¿el sistema puede operar?)

Es la primera barrera y la más dura. Agrupa varios controles:

- **Freno global (`paused` / global_mode).** Un interruptor maestro: si el sistema está en pausa,
  **todo** se bloquea aquí, sin importar la estrategia. Es el "freno de mano".
- **Kill-switch por capas.** El despacho real a TradersPost solo ocurre si se cumplen **todas** las
  capas: la variable de entorno `TRADERSPOST_ENABLED`, que `DRY_RUN` (entorno) esté en falso, que
  el perfil tenga TradersPost habilitado, y que el `dry_run` de la config no esté activo. Un perfil
  **solo puede RESTRINGIR** (nunca habilitar lo que el entorno apagó). Es fail-closed: ante la duda,
  no envía.
- **Mapa de símbolos.** El ticker recibido debe resolver a un símbolo de mercado válido; si no mapea,
  se bloquea.
- **Guardarraíles / cuarentena.** Una estrategia en cuarentena (`quarantined`) no despacha — y este
  control se respeta incluso para reversiones (una estrategia en cuarentena no cierra vía reversal).

**Por sí solo:** protege la cuenta a nivel sistémico. Es el que garantiza el "fail-closed" — si algo
está mal configurado o apagado, aquí se detiene todo antes de tocar el mercado.

### L2 — Temporal (¿buen momento, señal vigente?)

- **Frescura / staleness.** Si la señal llegó demasiado vieja (el `signal_ts` viene de
  `payload.time`), se descarta: una señal rezagada apunta a un precio que ya no existe. Evita operar
  sobre información caduca (retrasos de red, reintentos tardíos).
- **Ventana de sesión / hora.** Solo se opera dentro de la ventana horaria configurada por
  estrategia (todo en **ET**). Fuera de sesión → bloqueo. Sirve para evitar horas de baja liquidez o
  sin edge (el "edge por hora" del Laboratorio informa esta ventana).

**Por sí solo:** filtra por *cuándo*. No juzga la calidad de la señal, solo que llegue a tiempo y en
el horario correcto.

### L3 — Riesgo (¿seguro dado lo que ya tengo?)

- **Estado de posición.** Lee el estado estimado de la posición (FLAT / PENDING / LONG / SHORT /
  EXITING) para decidir si la acción tiene sentido.
- **`symbol_busy` (NX-09, L3.4).** El corazón de este nivel: **una sola posición por símbolo**. Si ya
  hay una posición o reserva activa en ese símbolo, una nueva entrada se **bloquea** — salvo que la
  estrategia tenga `allow_stacking` (opt-in) o sea una **reversión** (exenta). Esto es lo que permite
  correr **dos o tres estrategias sobre el mismo activo** sin que se pisen: **gana la primera que
  llega y pasa los filtros**, las demás se bloquean con `symbol_busy`.

**Por sí solo:** evita el apilamiento involuntario de riesgo en un mismo instrumento. No mira el
mercado, mira **tu exposición actual**.

### L4 — Calidad + Régimen (¿la señal es buena y el mercado acompaña?)

Este nivel tiene dos gates que corren juntos: **primero el régimen**, luego la **calidad**.

**Régimen (HMM · Kaufman Efficiency Ratio).** Clasifica el mercado en `trending_bull`,
`trending_bear`, `ranging` o `unknown`, **leído en una temporalidad superior** (1h por defecto,
independiente de la temporalidad de la entrada). Bloquea (`regime_not_allowed`) **solo si** la
estrategia definió `allowed_regimes` no vacío **Y** el régimen es conocido **Y** no está en la lista.
Dos salvaguardas clave: `unknown` **falla abierto** (si no hay datos suficientes, no bloquea — no
castiga la ignorancia), y `enabled` con lista vacía es un **no-op** (corre pero nunca bloquea, deja
aviso). Es el gate que activamos en RTY (`1h ∈ {trending_bull, trending_bear}`).

**QualityScorer (los 4 subscores).** Calcula un **score 0–100** como promedio ponderado de cuatro
sub-medidas, y bloquea (`score_below_minimum`) si el score queda por debajo del `score_minimum`
(70 por defecto, editable por estrategia). Los cuatro subscores:

- **`volume_relative`** — volumen actual contra su promedio reciente. Alto = participación real detrás
  del movimiento; bajo = movimiento "hueco". Fue el único filtro que sobrevivió out-of-sample en ES.
- **`atr_normalized`** — la volatilidad (ATR) normalizada. Ubica la señal en su régimen de volatilidad
  (¿mercado dormido o agitado?).
- **`vwap_position`** — posición del precio respecto al VWAP. Mide si entras a favor o en contra del
  precio promedio ponderado por volumen del día.
- **`time_of_day`** — el edge de la hora de la sesión (distinto de la ventana de L2: aquí es un
  *score*, no un gate binario).

**El estándar honesto (Anexo 25 §1-bis / NX-04):** el score **ya no parte en 100**. Si **no hay
filtros activos**, no hay calidad medida → la etiqueta es **UNKNOWN**, no HIGH. La taxonomía es
**UNKNOWN / LOW / MEDIUM / HIGH** (umbral HIGH por defecto 80). "Score 100" solo es confiable si
proviene de filtros reales; sin filtros = calidad desconocida.

**EMA-bias (filtro direccional — candidato).** Un gate de tendencia (EMA de TF superior, 1h/4h ·
20/50): con-tendencia = long sobre la EMA, short debajo. **Se estudia en el Laboratorio** como
candidato; **⚠ no se aplica a las estrategias contrarian** (como `GC5m_ContraNormal`), que por
diseño entran contra la tendencia.

**Por sí solo:** L4 filtra por *calidad de la señal* y *contexto de mercado*. Es el nivel más
"inteligente" y el que más se calibra.

### L5 — SL/TP por ATR (¿puedo protegerla?)

- **Stop-loss por ATR — OBLIGATORIO.** Se calcula el SL en función del ATR. **Una entrada sin SL
  válido → BLOCK** (`block_level=5`). Este es el invariante fail-closed más importante: **nunca se
  abre una posición sin stop**. El SL cumple el rol de **protección catastrófica** (que un mal trade
  no se coma la cuenta), no de optimización.
- **Take-profit por ATR.** Se calcula el TP. Como TradersPost **exige** un TP, la política es ponerlo
  **nominal y ancho** (un bracket que casi nunca capa antes de que cierre LuxAlgo), no como meta.

**Por sí solo:** garantiza que toda entrada tenga red de seguridad. Es la última barrera antes del
despacho.

---

## 3. Los filtros, en conjunto

Aquí está lo que no se ve mirando cada filtro aislado.

### 3.1 El orden importa (y por qué ese orden)

El pipeline corta **en el primer bloqueo**, así que el orden define **qué motivo de bloqueo ves** y
**cuánto cómputo se gasta**. Va de barato → caro y de sistema → mercado: primero lo que apaga todo
(L1), luego lo temporal (L2), luego tu riesgo actual (L3), y solo entonces se paga el cómputo de
calidad/régimen (L4) y el cálculo de SL/TP (L5). Una señal que llega en pausa global nunca gasta un
cálculo de ATR. En Analítica, el gráfico "¿en qué nivel se bloqueó?" te muestra dónde muere el flujo.

### 3.2 Sustractivos vs cambia-desenlace

No todos los filtros son iguales, y esta distinción es la base de toda la calibración:

- **Sustractivos** (calidad, régimen, EMA, ventana): solo **incluyen o excluyen** la operación. No
  cambian el resultado del trade que sí pasa — recortan *cuáles* operas.
- **Cambia-desenlace** (SL/TP): **cambian el resultado** del trade. Un SL puede convertir un ganador
  en perdedor (si lo saca antes de tiempo); un TP puede capar un ganador.

Por eso en el Laboratorio los sustractivos se miden re-agregando (¿mejora el PF/WR del subconjunto
que queda?) y los SL/TP se **re-simulan** trade a trade.

### 3.3 Interactúan — el conjunto no es la suma de las partes

Este es el punto más importante y el que motivó el "panel de config combinada" del visor. Como una
señal debe pasar **todos** los filtros, sus efectos **se solapan**: un filtro de calidad que quita
los trades de bajo volumen puede estar quitando *también* los trades que el gate de régimen habría
bloqueado. Por eso **el lift de un filtro medido por separado no se suma con el de otro** — apilados
dan un resultado distinto. La regla operativa: **evalúa siempre la configuración completa junta**, en
el orden correcto (sustractivos recortan el universo → SL/TP re-simulan sobre ese subconjunto → las
piernas ajustan la entrada), no filtro por filtro.

### 3.4 Cada estrategia arma su propio conjunto

Los filtros son los mismos, pero **cada estrategia los activa, desactiva y umbrala distinto**:
`score_minimum`, `allowed_regimes`, ventana horaria, pesos de subscores, piernas del escalonado,
`cancel_after`. Una estrategia de **tendencia** usa el gate de régimen y el EMA-bias; una
**contrarian** (GC) los omite y tiene otra economía de salida. Por eso "dos ES sobre el mismo activo"
tienen números totalmente distintos aunque compartan pipeline.

### 3.5 Las salidas rompen las reglas (a propósito)

Los cierres deben poder pasar siempre. Las exits están **exentas** del `symbol_busy` (L3), y los
cierres forzados / reversiones se despachan a **todos** los perfiles. La única barrera que las exits
sí respetan es L1 (una estrategia en cuarentena no cierra vía reversal). Esto evita quedar atrapado
en una posición que no se puede cerrar.

---

## 4. Cómo se calibran (el Laboratorio)

El pipeline **aplica** los filtros; el Laboratorio los **calibra** sobre datos reales:

- **Por separado:** el lift de cada filtro/umbral se mide **dentro y fuera de muestra** (70/30). Solo
  se confía en los que aguantan el examen out-of-sample; los que solo brillan in-sample son
  espejismos (marcados con ⚠ cuando el `n_out` es bajo).
- **En conjunto:** la "config combinada" del visor aplica todo junto en el orden correcto y muestra la
  curva de equity, el veredicto interpretado ("el PF baja pero el WR sube → riesgo, no calidad") y el
  costo — que es como de verdad se decide.
- **Objetivo:** *rentable sin arriesgar la cuenta* — máxima ganancia bajo un tope duro de 1% de
  riesgo por trade (SL ancho catastrófico + escalonado somero + sizing), con la guarda de que la
  expectancy siga positiva fuera de muestra.

---

## 5. Traza de ejemplo (una señal que aprueba)

1. **L1** — sistema no está en pausa, kill-switch en vivo, `NQ1!` mapea a `MNQ`, estrategia no en
   cuarentena. ✅ pasa.
2. **L2** — la señal llegó hace 3 s (fresca), y son las 10:30 ET (dentro de ventana). ✅ pasa.
3. **L3** — no hay posición ni reserva en el símbolo (`symbol_busy` libre). ✅ pasa.
4. **L4** — régimen 1h = `trending_bull` (en `allowed_regimes`); QualityScorer = 82 ≥ 70 → etiqueta
   **HIGH**. ✅ pasa.
5. **L5** — SL por ATR calculado (válido), TP nominal ancho. ✅ pasa.
6. → **APPROVE** → despacho con entrada escalonada (piernas someras) a TradersPost.

Si en cambio otra estrategia ya tuviera `MNQ` abierto, la señal moriría en **L3** con
`block_reason=symbol_busy` — y lo verías en Analítica sin llegar a gastar el cálculo de calidad ni de
SL.
