# Luxy — Especificación para recrear el dashboard
### Guía componente por componente · para Claude Code

> **Misión:** recrear este dashboard (el "estudio de riesgo" de una estrategia) dentro de NTEXECG, con todos los elementos descritos aquí. La fontanería del sistema (hooks de recompute, status lifecycle, persistencia, despacho real de webhooks) la resuelve Claude Code con acceso al repo; **este documento describe el dashboard en sí**: qué muestra cada pieza, cómo se calcula, cómo se comporta y cómo se ve.
>
> **Fuentes de verdad (adjuntas):**
> - `panel_palancas_multi.html` — el dashboard funcionando (referencia visual y de interacción). Recrear a partir de aquí (re-skin a dark, y **agregar el panel de Perfiles**, que aún no está en el prototipo).
> - `build_dashboard_data.py` + `estudio_estrategia.py` — el motor que calcula todo (produce el JSON que el dashboard consume).
> - `luxy_engine.py` — envoltorio de una estrategia + perfiles + payloads TradersPost.
>
> **Regla de oro:** todo dentro de muestra, con el **100% de los trades** (sin OOS). Los niveles se **derivan de los datos**; en el perfil principal no se editan a mano.

---

## 0. Arquitectura en dos capas

```
Motor (Python, ya existe)  ──►  LuxyParams (JSON)  ──►  Dashboard (front-end)
build_dashboard_data.build()      el "contrato"          recrea los componentes de abajo
```

El dashboard **no calcula lo pesado** (reconstrucción intrabar sobre miles de barras): lee el JSON del motor. Solo hace una **estimación client-side ligera** para el "Recalcular" y la cascada en vivo (§3).

En NTEXECG: **1 estrategia = 1 lista de operaciones = 1 entrada de datos**. El prototipo trae un selector multi-instrumento (6 estrategias) solo para demostración; en producción puede ser una sola, o el selector de estrategias que ya exista.

---

## 1. Contrato de datos (lo que el dashboard consume)

Por estrategia, el motor entrega este objeto (campos → componente que los usa):

```jsonc
{
  "pv": 50.0, "n": 121, "recon_ok": 119, "fragile": false, "notes": [],
  "mfe_max": 3312.5, "mae_min": -11125.0,          // dominio de la gráfica
  "trades": [ {"i":1,"mfe":1075,"mae":-812.5,"pnl":937.5,"long":false,"hr":9,"dow":1}, ... ],  // nube + estimación
  "base":   {"net":26288,"pf":1.55,"dd":-11750,"worst":-10162,"wr":79.3,"part":100},  // columna "crudo"
  "config": {"net":28031,"pf":1.69,"dd":-5258,"worst":-3574,"wr":80.9,"part":100},    // columna "con config" (validada motor)
  "regime": {"change":12.8,"pct_up":65,"label":"alcista"},                            // chip contexto
  "timestop": {"verdict":"descartado","buckets":[{"range":"0–30","n":..,"net":..}, ...],"why":"..."},
  "reco": {
    "sl_usd":3840,"tp_usd":3640,"be_usd":null,"l2_usd":380,"l3_usd":760,
    "sl_pts":76.8,"tp_pts":72.8,"l2_pts":7.6,"l3_pts":15.2,
    "alloc":[5,3,2],"fill2":62,"fill3":47,"why_alloc":"...","dir":"both",
    "zones":[ {"name":"Europa/Londres","hours":[2,3,4,5,6,7],"n":34,"net":18162,"wr":82.4,"pf":4.46,"losing":false}, ... ],
    "days": [ {"dow":4,"name":"Viernes","n":18,"net":-17275,"wr":50.0,"pf":0.30,"losing":true}, ... ]
  }
}
```

---

## 2. Componentes del dashboard (recrear uno por uno)

### A. Cabecera + chips de estado
- Título de la estrategia + subtítulo (nombre largo, n operaciones, `$PV/punto`, dirección recomendada).
- Chips: **validado · motor** / **estimación · aprox** (según si se tocó una palanca), **recon X/n**, **régimen: alcista +12.8%** (de `regime`), y **⚠** si `fragile`.
- Botones **Recalcular** (activo solo si hay cambios) y **Restablecer** (vuelve a la recomendación).

### B. Bloque comparativo — crudo → con config
Seis teselas, cada una `crudo → config` con su **Δ** en color: **Neto total · Profit factor ★ · Max drawdown · Peor trade · Participación · Win rate**. Fuente: `base` y `config`. Por defecto son los números **validados por el motor**; al mover palancas, "Recalcular" los sustituye por la **estimación** (§3) y cambia el chip a ámbar. Coloreado por "mejora": verde bueno, rojo peor, gris neutral (participación). Drawdown se muestra como magnitud (menos DD = mejor).

### C. Gráfica — recorrido vs. cierre (la central)
- Cada operación en su **punto cero** (entrada). Barra translúcida verde = **MFE** (arriba), roja = **MAE/pullback** (abajo); barra sólida = **cierre real** (verde/rojo). Fuente: `trades`.
- **Líneas de palancas** en su valor USD: TP techo, breakeven, escalonado (C1=0, C2=−l2, C3=−l3), SL backstop. Toggle prende/apaga cada una.
- **Barras completamente limpias**: los códigos van en una **columna a la derecha, fuera del plot**, cada uno con su código + **puntos** (índices) — `TP +73 · C1 0 · C2 −8 · C3 −15 · SL −77`. Puntos = `valor_usd / PV`. En divisas (PV gigante) se omiten los puntos.
- Eje **USD** a la izquierda, **pts** a la derecha (solo si `PV ≤ 1000`). **Leyenda al pie** con el valor USD de cada línea. Tooltip por operación (MFE/MAE/cierre).

### D. Controles de palancas (en USD, debajo de la gráfica en 3 columnas)
1. **Vistas de la nube**: toggles MFE / MAE / cierre. **Dirección**: ambos / largos / cortos (default `reco.dir`). **Lector en vivo**: a cuántas operaciones toca cada palanca y su efecto neto aprox.
2. **Salida**: **TP techo** (toggle + slider USD) y **Breakeven** (toggle + slider; apagado si `be_usd=null`).
3. **Entrada y stop**: **Escalonado** — muestra el **reparto derivado** (p.ej. `5/3/2`) como salida del estudio **no ajustable**, con **banner de justificación** (`why_alloc`: "…llega a C2 el 62%… reparte 5/3/2…"); C1 es la entrada fija en 0; solo los **niveles C2/C3** son sliders. Y **SL backstop** (toggle + slider).
- Rangos de los sliders derivados por instrumento de `mfe_max`/`mae_min`.

### E. Sesiones / zonas de bolsa (tabla + bloqueo)
Filas por zona NY (`reco.zones`): **toggle de bloqueo · nombre · n · neto · WR**. Las `losing` marcadas **"pierde"** (ámbar). **Por defecto no bloquea nada** — es diagnóstico; bloquear recalcula (quita esas horas del filtro). Nota visible: bloquear solo con razón real (no por sobreajuste).

### F. Días de la semana (tabla + bloqueo)
Igual que sesiones, con `reco.days`. Resaltar que el **viernes** (NFP) suele ser el peor día.

### G. Time-stop (diagnóstico)
`timestop.buckets`: neto por duración (barras) en mini-barras, + **veredicto: descartado (sesgo de supervivencia)** con `timestop.why`. **No es palanca aplicable** — es solo diagnóstico (la lección: las operaciones largas pierden, pero cortarlas quita recuperaciones).

### H. Integrador — cascada de palancas (sección aparte, debajo)
Apila las palancas en orden **riesgo → filtros**: `Crudo → +SL → +escalonado → +TP → +breakeven → +dirección` (cierra la config), y luego **atenuados**: `+ bloquear sesiones/días que pierden` (diagnóstico). Por fila: **barra de neto**, **PF · DD** debajo, y a la derecha el **Δ del paso** — neto **y drawdown** (el Δ de DD es clave: el SL a veces no sube el neto pero corta el DD a la mitad). Se recalcula en vivo con las palancas de arriba. Endpoint = la **estimación** (≈10% bajo el validado del motor, anotarlo).

### I. Perfiles — principal + 4 secundarios (NUEVO, agregar)
La pieza que falta en el prototipo. Un selector/tabla de **5 perfiles**:
- **Principal** = parámetros calculados, tamaño completo (`alloc` tal cual, 10 micros = 1 mini).
- **4 secundarios** = misma forma, distinto **tamaño por cuenta** (propia vs fondeadoras). Por perfil mostrar: `size_scale`, **micros escalados** (`scale_alloc`), **peor-caso por operación** (`worst_case_loss`), **caps** (max_contracts, max_loss_per_trade, max_daily_loss), y su **webhook**. Cálculo en `luxy_engine.build_profile`.
- Mostrar el **payload TradersPost** por perfil/lado (`traderspost_payloads`) en la sub-pestaña Export.
- **Insight a exhibir:** con SL ancho (~$384/micro en ES), una fondeadora con tope de $500/op solo aguanta 1 micro — el motor ya lo calcula; el panel debe hacerlo evidente.

---

## 3. Motor de estimación client-side (para "Recalcular" y la cascada)

El dashboard recalcula en vivo sin llamar al motor, con esta aproximación (ver `estimate()` / `metricsFor()` en el HTML):

```js
// por operación, exit en USD:
let e = d.pnl;
if (SL_on && d.mae <= -SL_usd)      e = -SL_usd;            // pegó el backstop
else if (TP_on && d.mfe >= TP_usd)  e = TP_usd;             // pegó el techo
else if (BE_on && d.mfe >= BE_usd && d.pnl < 0) e = 0;      // breakeven salvó una perdedora (aprox)
// escalonado (reparto derivado alloc):
if (SC_on) { const ep=e/PV, MES=PV/10; e = Σ_niveles q_i·(ep + L_i/PV)·MES  para niveles llenados (mae ≤ -L_i) }
// filtros: descartar si la operación cae en sesión/día bloqueado o no coincide con la dirección
```

Luego métricas (net/PF/DD/participación) sobre las operaciones conservadas. **El número oficial siempre es el del motor** (reconstrucción intrabar); la estimación queda a ~pocos % y sirve para explorar. Etiquetar claramente validado vs estimación.

---

## 4. Diseño (tema oscuro NTEXECG)

El prototipo está en claro; portar los tokens `:root` a dark navy:

| claro | dark |
|---|---|
| `--paper #F4F6F7` → `#0d1117` | `--panel #FFFFFF` → `#161b22` |
| `--ink #15181C` → `#e6edf3` | `--muted #6B7278` → `#8b949e` |
| `--line #E4E8EB` → `#30363d` | verdes/rojos y accents de palanca (`--tp/--be/--scin/--sl`) → **se mantienen** |

Orden vertical: cabecera+chips → comparativo → gráfica → controles (3 col) → sesiones → días → time-stop → integrador → **perfiles**. Responsivo: 3→2→1 columnas.

---

## 5. Normas a respetar (resumen)

- **Dentro de muestra, 100% de los trades. Sin OOS.**
- Niveles/reparto/dirección **derivados** (no editables en el principal); el escalonado lleva **banner de justificación** y **no es ajustable** (solo niveles C2/C3).
- **USD interno**; puntos en índices (`PV≤1000`), USD en divisas.
- **Sesiones/días = diagnóstico**: default sin bloqueo; bloquear solo con razón real (candidatas = neto<0, n≥8).
- **Régimen = contexto**; **time-stop = descartado** (supervivencia).
- **Fragilidad ⚠**: recon<90%, flip de signo, o mejora >3×.
- El integrador siempre muestra **Δ de drawdown** (la tesis es balance riesgo/beneficio, no máxima ganancia).
- **Perfiles secundarios** solo re-escalan tamaño + caps por cuenta; cualquier override (p.ej. SL más ajustado para fondeadora) se marca como desviación de la config estudiada.

---

*Recrear a partir de `panel_palancas_multi.html`; el motor (`build_dashboard_data.py`/`estudio_estrategia.py`/`luxy_engine.py`) produce el JSON del §1. La integración con NTEXECG (persistencia, recompute-on-signal, status lifecycle, despacho real) la conecta Claude Code.*
