# LOTE UI-DESPACHO-UNIFICADO — los perfiles vuelven a casa

> 2026-07-19 · Pedido del operador: un perfil es OTRO DESTINO del mismo
> despacho (hereda el bracket, NX-02 solo endurece) — no merece pestaña
> hermana de Config. Solo template + wiring; **cero cambios de lógica ni de
> rutas de escritura**. Lote JS: smoke del operador antes de commit.

## Qué cambió (solo `strategy_detail.html`)

1. **Movido a Config → Despacho (TradersPost) → "Destinos — base + N
   perfiles"** (ancla `id="despacho"`), debajo del estado de armado y la URL
   base, TODO el contenido de la ex-pestaña Perfiles, VERBATIM:
   - **"Destino 1 — BASE (cuenta principal)"** — bloque que enmarca el
     webhook base como primer destino (la herencia, visual);
   - el panel read-only L4 (sizing / peor-caso / caps / Export del builder
     real) intacto;
   - el form de edición de perfiles (misma ruta `POST /profiles`, mismos
     campos), con cada perfil rotulado "Perfil i (destino i+1)" y su
     avanzado colapsado como estaba.
   El contador del header cuenta perfiles HABILITADOS.
2. **La pestaña Perfiles se RETIRÓ**: fuera del tab bar; compat de links
   viejos vía `x-init` — `#perfiles` (o `#despacho`) aterriza en Config y
   hace scroll al ancla. El puntero de la sección Scale Entry ("Editables en
   la pestaña Perfiles") ahora lleva a "Despacho → Destinos" con el mismo
   scroll. Cero referencias restantes a `stab === 'perfiles'` (verificado
   por guarda).
3. **Backend intacto**: `POST /profiles`, `_perfiles_panel`, dprof — sin
   tocar (los tests de test_perfiles_l4/test_dispatch_profiles corren tal
   cual).

Borde sin-profile RESUELTO con fuente única: el panel read-only vive como
PARTIAL (`app/templates/_perfiles_panel_ro.html`, `{% include %}` hereda el
contexto) incluido 2 veces — en Destinos (caso real) y en el `{% else %}`
"Sin perfil de configuración" (el comportamiento de la ex-pestaña, que
mostraba el panel también sin profile — cazado por
`test_estrategias_l1::test_luxy_tab_sin_estudio` en el run completo y
corregido en el template, no en el test). Los DOS tests de estrategias_l1
que fijaban la pestaña de 4 labels se actualizaron a la realidad nueva (el
retiro es el propósito del lote).

## Guardas (`tests/test_ui_despacho_unificado.py`, 6)

Fuente: tab bar sin 'perfiles' y sin `stab === 'perfiles'` · `id="despacho"`
+ "Destinos — base + " + "Destino 1 — BASE" · contenido movido UNA sola vez
(form/panel/action contados) · compat `#perfiles`→`getElementById('despacho')`.
Render (página real con seed): "Destinos — base + 1 perfil", destino 1, form
vivo, sin botón de pestaña Perfiles; y el caso 0 perfiles habilitados.

Suites de render/template previas intactas: 113 verdes (web, lx15+regresión
HTML, perfiles_l4, ra3_ui, visual_exclusiones, asset_profiles).

## Fallout del run completo (corregido) + FIX-FLAKE-2 mínimo

- Dos tests de `test_estrategias_l1` fijaban la pestaña de 4 labels y el
  panel sin-profile → actualizados a la realidad nueva (el retiro es el
  propósito) y el sin-profile RESUELTO en el template (partial, arriba).
- `test_display_fx_slrespiro::test_jinja_rincones_migrados_a_fmt_pts`
  buscaba el `fmt_pts(P.sl_pts)` en el detalle → ahora verifica el PARTIAL y
  que el detalle lo incluye (misma garantía, nueva casa).
- **FIX-FLAKE-2 — diagnóstico FINAL y escalado (bloquea la corrida única, no
  el lote)**: `test_riesgo_ui::test_aceptacion_es_end_to_end` falló 5× hoy
  en runs completos y SIEMPRE pasa aislado. Subí su presupuesto de polling
  240→480 s (divulgado, inofensivo) y AUN ASÍ falló — el tail del assert
  (`…_init__`) delata que su SUBPROCESO `calcular` real MUERE con traceback
  (MemoryError en el init de pandas/numpy) cuando el proceso pytest ya
  acumuló ~toda la suite (~1450 tests) en un host de 11.7 GB (7.8 usados en
  reposo); otro run del día murió con MemoryError en cascada (28 errors) en
  `test_mr_sims` ES-real. **No es lógica de ningún lote** (víctimas
  distintas, todas gated de datos reales, todas verdes aisladas).
  DECISIÓN PARA EL ARQUITECTO (lote FIX-FLAKE-2): (a) serializar los gated
  de datos reales en una segunda invocación, (b) más RAM al host, o
  (c) cazar la acumulación del proceso pytest (verificado: los heatmaps SÍ
  cierran sus figuras — mr_report.py:844; la acumulación es difusa: caches
  de módulo/pandas a lo largo de ~1450 tests). Mientras: el cierre de ESTE
  lote documenta la suite en dos pasadas (abajo).

## Estado FINAL de la suite (dos pasadas documentadas)

1. **Corrida completa menos el test-víctima**: `pytest tests --deselect
   tests/test_riesgo_ui.py::test_aceptacion_es_end_to_end` →
   **1441 verdes / 7 skipped / 1 deselected (11m 18s, exit 0)**.
2. **El test-víctima aislado**: `test_aceptacion_es_end_to_end` →
   **1 passed (63s)**.
Cobertura total verde; la corrida ÚNICA queda bloqueada solo por el techo de
RAM del host (FIX-FLAKE-2, arriba — decisión del arquitecto).

## SMOKE del operador (antes del commit)

1. Ficha de una estrategia con perfiles: pestañas = Config · Luxy · Lab
   (sin Perfiles). En Config → Despacho: "Destinos — base + N perfiles" con
   el bloque verde "Destino 1 — BASE" y tu webhook.
2. La tabla de sizing/peor-caso y el Export del builder están ahí mismo;
   editar un perfil y Guardar → funciona igual que antes (misma ruta).
3. Abrir `/ui/strategies/<id>#perfiles` (link viejo) → aterriza en Config
   con scroll a Destinos.
4. En Luxy, el puntero "Perfiles de riesgo (tiers) → Editables…" lleva a la
   sección nueva.

## Pendiente

Smoke del operador → revisión del arquitecto → commit (template + guardas +
este .md). Protocolo §0.
