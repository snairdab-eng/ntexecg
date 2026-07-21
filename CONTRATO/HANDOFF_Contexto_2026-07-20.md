# HANDOFF — Contexto al 2026-07-20 (lunes: LA jornada de mercado vivo)

> El día en que el sistema dejó de ser una teoría bien testeada y pasó a ser
> una máquina verificada contra el broker real. Tres P0 encontrados,
> arreglados y demostrados con órdenes reales.

## 1. LO QUE PASÓ HOY (en orden)

1. **Auditoría final de la etapa Fable** entregada (608 líneas,
   `AUDITORIA_FINAL_2026-07-19.md`): 2 P0 de código (A-1 ambigüedad de red,
   B-1 intent-first) + ~30 hallazgos menores. Ambos P0 **cerrados** el mismo
   día (LOTE P0-AMBIGUEDAD), con un hallazgo extra de regalo: aliasing en
   `set_rearm_state` que hacía fantasma el UPDATE (deepcopy).
2. **Primera prueba T2 con mercado abierto** → destapó **dos P0 que ninguna
   auditoría de código podía ver**, porque no eran errores de código sino
   supuestos sobre el broker:
   - **El exit no cerraba la posición completa**: viajaba con la `quantity`
     de la alerta (1) sobre una posición de 5 → cerraba 1 y dejaba 4.
   - **La escalera nunca llegó al broker**: las piernas C2/C3 se despachaban
     con `action:"buy"` y TradersPost las **ignora en silencio** cuando ya
     hay posición abierta.
3. **Hallazgo colateral**: FILTROS-OFF nunca se había aplicado (el payload
   en vivo delató `filters_active:true, score 58/55`). Corregido: 7/7
   passthrough.
4. **Sondas directas al webhook de TradersPost** (saltándose NTEXECG) para
   establecer la semántica real → documentada en
   `TRADERSPOST_Semantica_Verificada_2026-07-20.md`. Resultado: `add` es la
   acción correcta, **sin `sentiment`**, y acepta bracket/cancelAfter.
5. **Dos lotes de fix implementados, desplegados y VERIFICADOS EN VIVO**:
   P0-EXIT-PARCIAL (exits sin `quantity` ⇒ aplanan completo) y ESCALERA-ADD
   (piernas i>0 con `action:"add"`).

## 2. VERIFICADO CON ÓRDENES REALES (la tabla que importa)

| Qué | Evidencia |
|---|---|
| Exit cierra COMPLETO | pidió cerrar 1 con 5 abiertos → cerró los 5 |
| `cancel:true` mata piernas (FIX-D3) | con una límite viva real en 7495 |
| **La escalera EXISTE** | C1 5@mercado + C2 3@7504.75 + C3 2@7494.25, las tres llenaron |
| Reparto por perfil en escalonada | base 5/3/2 (10 micros) · APEXsim 1/1/0 (2) |
| Mejora del precio de entrada | 7511.75 (solo C1) → **~7507.75** promedio |
| `cancelAfter: 3600` en el cable | presente en cada pierna límite (RA-2a) |
| Precios al tick (FIX-D2) | 7504.75 / 7494.25 exactos |
| `symbol_busy` (NX-09) | bloqueó 3 de 3 entradas apiladas |
| L1.6 datos vivos | bloqueó entrada con el bridge caído |
| Filtros N4 fuera | `filters_active:false`, score passthrough |

## 3. ESTADO DEL SISTEMA AL CIERRE

- **7/7 sin filtros N4** · **ES con escalera en `execute`** (las demás en
  `design_only` tras la mitigación de la mañana — revisar cuáles reactivar).
- **Re-armado: DEBE quedar OFF** en ES y RTY (el smoke de RA-3 las dejó
  encendidas; RTY además quedó en DRY_RUN). **CONFIRMAR mañana.**
- Broker limpio: todas las posiciones de prueba cerradas.
- Suite 1463 verde. Huérfanos históricos: se cerraron solos por bracket
  (no hubo exposición), pero **el rango 1–20 julio queda marcado como datos
  contaminados** para la reconciliación ExecutionResult vs estudio.

## 4. QUÉ FALTA — mañana, en orden

1. **COMMIT de lo pendiente** (el árbol acumula ESCALERA-ADD y docs). Y
   `git status --short` antes y después de cada `git add`.
2. **Confirmar `rearm.enabled = false`** en ES y RTY (chequeo por script o
   botón de Config). Decidir si RTY vuelve de DRY_RUN a envío armado.
3. **LOTE REARM-LISTO** (prompt entregado, REVISIÓN PROFUNDA): cierra B-2
   (limpiar `risk_plan_json["rearm"]` al cerrar la posición) y B-4 (carrera
   job vs exit), y entrega `scripts/observa_rearm.py` + protocolo de
   verificación en vivo. Es el prerequisito para probar el re-armado.
4. **Verificación en vivo del re-armado** (tras el lote): encender rearm en
   ES, lanzar escalonada, esperar ~65 min y confirmar que al minuto 62
   aparece la pierna con `client_id -r2`, mismo precio, y **jamás dos vivas
   a la vez**.
5. **LOTE HIGIENE** con lo que se acumuló:
   - El **Aplicar de palancas reescribió el modo de escalera en silencio**
     (ES volvió sola a `execute`) — cambiar modo de ejecución sin decirlo es
     justo lo que llevamos semanas desterrando.
   - **A-6**: el re-escalado por perfil está inerte en entrada SIMPLE (la
     "cuenta chica" recibe el tamaño íntegro) — confirmado en vivo hoy.
   - **FIX-FLAKE-2**: el MemoryError de los tests gated bajo carga
     (decisión ya tomada: serializar los gated en segunda invocación).
   - Resto de P1/P2 de la auditoría final: A-2 (dedupe tras re-key), A-3
     (tick en `recompute_bracket` por perfil), A-5 (cota de `quantity`),
     E3b del flujo principal, C-1 (loop de "Estudio de riesgo →"), etc.
6. **Huecos declarados sin verificar**: `add` sobre posiciones **SHORT**
   (misma regla implementada, sonda en vivo pendiente) y el alcance del
   `cancel`/`exit` con **ticker compartido** en una misma cuenta (T10 —
   regla vigente: jamás dos estrategias del mismo ticker vivas a la vez).
7. **Tareas de operador de siempre**: firewall a IPs de Cloudflare, rotar
   tokens de webhook (viajaron por chats), `{{interval}}` en las alertas de
   LuxAlgo, bajar los respaldos fuera del server.

## 5. LA LECCIÓN DEL DÍA (para el protocolo)

Dos auditorías de código, un fixture de oro con valores calculados a lápiz y
1455 tests verdes **no vieron ninguno de los dos P0 grandes** — porque
ninguno era un error de código: eran supuestos sobre el comportamiento del
BROKER que nadie había verificado nunca.

> **Ninguna cadena de despacho puede declararse correcta sin haberla visto
> ejecutar contra el broker real, al menos una vez, con los ojos.**

Y el corolario práctico que resolvió el segundo P0 en 5 minutos tras semanas
de suposiciones: **cuando la duda es sobre un sistema externo, no leas la
documentación — mándale sondas y mira qué hace.**
