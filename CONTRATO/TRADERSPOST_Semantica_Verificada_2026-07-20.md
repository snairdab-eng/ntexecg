# SEMÁNTICA DE TRADERSPOST — verificada empíricamente · 2026-07-20

> Sondas directas al webhook de TradersPost (cuenta paper, MESU2026, mercado
> abierto), saltándose NTEXECG para aislar el comportamiento del broker.
> Esto NO es documentación leída: es comportamiento observado con órdenes
> reales. Fuente de verdad para el diseño del despacho.

## Contexto de las sondas

Posición abierta previa: 3 micros LONG (entrada por NTEXECG, C1 a mercado)
con bracket TP 7569.50 / SL 7419.50. Mercado ~7508–7519.

| # | Payload enviado | Respuesta | ¿Creó orden? |
|---|---|---|---|
| A | `action:"buy"` + `orderType:"limit"` + `limitPrice:7505` + `sentiment` | `success:true` | **NO** — ignorada en silencio (enviada 2×, cero órdenes) |
| B1 | `action:"add"` + límite + **`sentiment`** | **`success:false`** `invalid-sentiment-action` | NO (rechazo explícito) |
| B2 | `action:"add"` + límite 7500, **sin** `sentiment` | `success:true` | **SÍ** — orden de trabajo visible; luego LLENÓ y la posición pasó de 3 → 5 micros (promedia) |
| C | `action:"add"` + límite 7495 + `stopLoss` + `takeProfit` | `success:true` | **SÍ** — orden de trabajo; el bracket se ajustó al nuevo tamaño |
| D | `action:"exit"` **sin** `quantity` + `cancel:true` (vía NTEXECG) | `success:true` | Aplanó los **5** micros **Y** canceló la límite pendiente de 7495 |

## Reglas establecidas (con evidencia)

1. **`success:true` del webhook significa "recibido", NO "orden creada".** La
   sonda A devolvió éxito y no generó nada. Nunca confiar el éxito del
   despacho a la respuesta HTTP.
2. **Un `buy`/`sell` con posición ya abierta se IGNORA en silencio.** Es la
   causa raíz del P0-2: las piernas C2/C3 nunca llegaron al broker en toda
   la vida del sistema.
3. **Para sumar a una posición existente hay que usar `action:"add"`**, y
   `add` **NO admite `sentiment`** (rechazo explícito). Sí admite
   `orderType`, `limitPrice`, `cancelAfter`, `stopLoss` y `takeProfit`.
4. **Las piernas `add` promedian la posición** y el bracket se ajusta al
   tamaño total — el comportamiento que la escalera necesita.
5. **`exit` sin `quantity` aplana la posición COMPLETA real del broker**
   (P0-EXIT-PARCIAL, verificado: se pidió cerrar 1 con 5 abiertos y cerró
   los 5); con `quantity` es cierre PARCIAL exacto.
6. **`cancel:true` en el exit cancela las órdenes de trabajo del ticker**
   (FIX-D3 verificado en vivo: la límite de 7495 murió con el exit).

## Especificación del fix pendiente (P0-2, ESCALERA)

En `PayloadBuilder.build_scaled` y `build_rearm_leg`, para las piernas i>0
(C2/C3 y re-armadas):

- `action` = **`"add"`** (hoy repite `signal.action`).
- **Omitir `sentiment`** (hoy lo incluye → rechazo garantizado).
- Conservar `orderType:"limit"`, `limitPrice` (al tick), `cancelAfter`,
  `stopLoss`, `takeProfit`, `extras`.
- C1 sigue con `buy`/`sell`: es la que abre la posición.
- **Pendiente de verificar**: comportamiento de `add` en posiciones CORTAS
  (las sondas fueron sobre un long). Probar antes de dar el fix por cerrado.

## HITO — la escalera EXISTE en el broker (2026-07-20, tras desplegar el fix)

Primera entrada escalonada real de la historia del sistema, verificada en las
DOS cuentas:

| Destino | C1 (mercado) | C2 (límite) | C3 (límite) | Bracket |
|---|---|---|---|---|
| base | 5 micros, llenado | 3 @ 7504.75 VIVA | 2 @ 7494.25 VIVA | TP 7568.00 / SL 7425.00 (+10s) |
| APEXsim | 1 micro, llenado | 1 @ 7504.75 VIVA | — (reparto 0) | mismo bracket (+2s) |

Confirma de una vez: `add` crea las órdenes de trabajo · el reparto por
perfil escala correctamente en escalonada (cada cuenta con su tamaño) · el
bracket común cubre el total potencial · `cancelAfter` corriendo sobre las
piernas. El corazón del diseño Luxy — mejorar el precio de entrada con
compras escalonadas — pasó de estudio a ejecución real.

## Estado verificado del sistema al cierre de la jornada

- P0-EXIT-PARCIAL: **arreglado y verificado en vivo** (cierra completo).
- FIX-D3 (cancel de piernas): **verificado en vivo** con pierna real.
- FIX-D2 (tick) y RA-2a (cancelAfter): verificados en los payloads reales.
- P0-2 ESCALERA: **diagnosticado con evidencia**, fix especificado, sin
  implementar.
- Mitigaciones vigentes: 7/7 en `design_only`, 7/7 sin filtros N4,
  re-armado a apagar en ES y RTY.
