# HANDOFF — Contexto al 2026-07-19 (sábado noche)

> Suite: 1281 verdes / 7 skipped, determinista. Todo commiteado y desplegado.

## Lo cerrado esta jornada (17-19 jul)

- **Auditoría total de Luxy + FIXTURE DE ORO** (26 tests con valores a lápiz,
  permanente): aritmética verificada al centavo en toda la flota; dos
  predicciones falsificables cumplidas 7/7 (GC crudo).
- **FIX-D**: el modelo pierna-más-profunda-que-stop corregido en 4 rutas
  (exit≈fill); $0 fantasma verificado. Destapó: NQ OOS honesto NEGATIVO.
- **SL-INSENSIBLE**: legítimo — el contador mentía contando excluidos por
  dirección. Ahora: desglose del peor trade pierna a pierna + contador con
  exclusiones declaradas.
- **DISPLAY-FX + SL-RESPIRO + VISUAL-EXCLUSIONES + PIERNAS-CLARIDAD**: deuda
  de display saldada — universos etiquetados, FX en ticks, slider SL respira
  1.25× con muesca, exclusiones atenuadas con fuente única, panel Piernas
  con conversión ATR/ancla/veredicto arriba.
- **Decisiones de flota ejecutadas**: NQ RETIRADA (OOS −$2,061, PF 0.59) ·
  GC con la config del operador ambos-lados/SL-57pts APLICADA vía gate
  (frase roja del flip aceptada; semáforo verde legítimo, geometría sana).
- Flota: ES-CN 🟢 · RTY · GC (config operador) · 6J mínima en paper;
  ES-CS y 6E pausadas; NQ retirada.

## En curso

- **RA-2b sub-paso 2** (estado persistente risk_plan_json["rearm"]) —
  prompt entregado a Fable/Claude Code; reporte pendiente → revisión del
  arquitecto. Después: sub-paso 3 (inferencia de precio, prompt por
  redactar), 4 (reglas R-RA9), 5 (RearmJob — revisión profunda), 6
  (adversariales) y RA-3 (UI). Primer candidato a encender: RTY (🟢, 2h).

## Plan del DOMINGO 20

1. **18:15 ET (mercado recién abierto)** — pruebas T2/T3 con sintética
   (kit PowerShell vigente): T2 escalonada ES → verificar 3 órdenes y
   `cancelAfter: 3600` EN EL PAYLOAD (verificación RA-2a pendiente) →
   cotejar precios al tick → T3 exit con piernas vivas → cancel:true las
   mata en TradersPost + audit EXIT_CANCEL_LEGS. Son los dos prerequisitos
   de demo para encender RA-2b.
2. Reporte de Fable del sub-paso 2 → revisión → commit → sub-paso 3.
3. (Opcional sábado/domingo) B.9 fin de semana: sintética con mercado
   cerrado → BLOCK market_data_not_active (fail-closed de datos vivos).

## Pendientes de operador (sin cambios)

Firewall a IPs Cloudflare · rotar tokens de webhook al cerrar la fase de
pruebas · backups Postgres (+ restauración de prueba) · {{interval}} en
alertas LuxAlgo · T10 (alcance del cancel con ticker compartido).
