# HANDOFF FINAL — Etapa Fable 5 (arquitecto) · 2026-07-16 → 2026-07-19

> Escrito por el arquitecto (Fable 5 en Cowork) al cierre de su ventana.
> Para: el operador y cualquier arquitecto/implementador futuro (humano o IA).
> El protocolo, los roles y las lecciones están aquí; el detalle técnico vive
> en los LOTE_*.md y AUDITORIA_*.md de esta carpeta. Léeme primero.

## 1. QUÉ ES ESTO (una frase por capa)

NTEXECG: gateway de ejecución fail-closed entre señales de LuxAlgo
(TradingView) y TradersPost (cuentas paper), con motor de estudios propio
(Luxy) que deriva la gestión de riesgo (backstop, escalera, TP, lado,
ventanas, re-armado) de las listas de operaciones, y la aplica a la config
viva SOLO mediante puentes supervisados con gate y auditoría. La misión no
es mejorar la señal: es controlar el riesgo y dejar huella honesta de todo.

## 2. PROTOCOLO DE TRABAJO (lo que hizo funcionar esta etapa — conservar)

- Roles: OPERADOR (decide, commitea, smokea, verifica en demo) ·
  ARQUITECTO (specs, prompts por lote, revisión — profunda si toca
  despacho/motor/datos, estándar si no) · IMPLEMENTADOR (Claude Code:
  implementa, reporta §0 con evidencia archivo:línea, JAMÁS commitea).
- Reporte §0 por lote: qué, causa raíz, evidencia, tests, suite COMPLETA en
  una corrida, change set exacto. Lotes JS: node --check + Jinja + guarda
  de render + SMOKE del operador antes de commit.
- Deploy: git push (NTDEV) → server: git pull, limpiar __pycache__,
  systemctl restart ntexecg (+ alembic upgrade head SOLO si hay migración).
- Ante conflicto con un invariante: el implementador ESCALA, no resuelve.

## 3. LO CONSTRUIDO EN ESTA ETAPA (16-19 julio, ~30 lotes, suite 1004→1441)

**Confiabilidad y verdad:**
- FIX-FLAKE: suite determinista (Selector loop win32 + shim subprocesos).
- Auditoría E2E del despacho (07-15) CERRADA: D-1 cuarentena con cota ·
  D-2 redondeo al tick + JSON fijo-decimal · D-3 cancel:true en exits ·
  D-4/D-4-bis precisión Numeric(20,10) señal→registro.
- AUDITORÍA TOTAL DE LUXY con FIXTURE DE ORO (26 tests con valores a lápiz,
  permanente): la aritmética verificada al centavo; dos predicciones
  falsificables cumplidas 7/7 contra CSV crudo.
- FIX-D: el modelo pierna-más-profunda-que-stop corregido (exit≈fill, 4
  rutas) — destapó que NQ tenía OOS negativo bajo el ⚪.
- BUG-HONESTIDAD: semáforo aplica LX-7 (4 de 7 mentían), Δ re-armado vs 1h,
  geometría stop-en-escalera visible (banner+gate+auditor).
- SL-INSENSIBLE: legítimo, pero el contador contaba excluidos — ahora
  desglose del peor trade pierna a pierna y exclusiones declaradas.
- Deuda de display SALDADA: universos etiquetados, FX en ticks en todas
  partes, slider SL respira 1.25× con muesca, exclusiones atenuadas con
  fuente única, panel Piernas con conversión/ancla/veredicto.
- FILTROS-OFF: el N4 (score/régimen) fuera de producción por decisión de
  misión; vive pausado en el Lab. FIX-FX-BACKSTOP: conversión USD→pts única
  con rejilla del tick + fail-honest sub-tick + auditor.
- LX-15 completo: aplicar-lo-que-ves + C1 móvil (cable fail-honest).
- UI-DESPACHO-UNIFICADO: perfiles como "destinos" dentro de Config.

**El RE-ARMADO (RA-0 → RA-3, completo en código, APAGADO en producción):**
- Estudio: panel Piernas con curva de llegada, veredicto económico por
  estrategia (RA-0v3) y constantes con evidencia. RA-1: modo re-armado
  informativo en la tabla de cortes.
- RA-2a: cancelAfter (1-3600s) en toda pierna límite — TTL bajo control.
- RA-2b (6 sub-pasos): config→estado persistente→inferencia de precio
  (calendario Globex)→motor R-RA9→RearmJob (mismo gate que entradas,
  idempotente, E3/E3b: ambigüedad MATA — jamás doble orden)→adversariales
  + reconstrucción desde AuditLog. ~130 tests propios.
- RA-3: sembrado vía Aplicar con guard server-side del veredicto (🟢
  exige checkbox + gate ámbar; Config solo APAGA), ciclos en Posiciones.

**Flota (estado al cierre):** ES-ConfNormal 🟢 legítimo (ancla) · RTY 🟢
(candidata única a re-armado, max 2 ciclos) · GC con config del operador
(ambos-lados/SL-57pts, aplicada con frase roja — flip declarado) · 6J
mínima (monocultivo cortos, ⚪ perpetuo, escalera ON como prueba) ·
ES-CS y 6E PAUSADAS · NQ RETIRADA (OOS honesto negativo).

**Resiliencia:** respaldo NTDEV (ZIP verificado + capa git de CONTRATO/
listas/bridge) · pg_dump semanal en cron con restauración SIMULADA y
verificada (4.49M barras) · fixture de oro como examen permanente del motor.

## 4. LO QUE FALTA (en orden)

1. **T2/T3 en mercado vivo** (quedó agendado para la reapertura): escalonada
   de ES con cancelAfter visible en el payload + exit que cancela piernas.
   SON PREREQUISITO para encender el re-armado.
2. **Encender rearm en RTY** (checkbox+gate) SOLO tras T2/T3 → observar los
   primeros REARM_LEG/KILL en demo. Después: T10 (alcance del cancel con
   ticker compartido) antes de pensar en dos estrategias del mismo símbolo.
3. **Smokes pendientes** si no se corrieron: RA-3 (7 pasos) y UI-DESPACHO
   (4 pasos) — listas en el chat del arquitecto y en sus LOTE_*.md.
4. **FIX-FLAKE-2** (decidido: opción a — serializar los gated de datos
   reales en segunda invocación pytest; el host tiene techo de RAM).
5. **AUDITORÍA FINAL** (prompt entregado al operador) → triage → primeros
   lotes de la siguiente etapa.
6. **Fase de observación (la FASE, no un paso):** ritual semanal — lista
   nueva → integrar → Calcular las 7 → semáforos → ExecutionResult vs
   estudio. La demo decide promociones; jamás los backtests solos.
7. **Tareas de operador abiertas:** firewall del origin a IPs de Cloudflare
   (pesa más desde FIX-D1) · rotar tokens de webhook al cerrar pruebas
   (viajaron por chats) · {{interval}} en las alertas de LuxAlgo ·
   verificación periódica de respaldos descargados fuera del server.

## 5. PRINCIPIOS GANADOS (la doctrina de la etapa — heredarla)

1. **Una verdad, una fuente**: todo número que aparece dos veces debe salir
   de la misma función. Las tres cazas grandes del operador (TZ, semáforo,
   geometría) fueron divergencias de fuentes.
2. **Fail-closed en despacho, fail-honest en pantalla**: bloquear ante lo
   ilegible; declarar universo/exclusiones/anclas ante el operador. "Jamás
   en silencio."
3. **La asimetría de la misión**: perder un fill < duplicar tamaño. Rige
   E3/E3b, la ventana ciega, y toda ambigüedad de red.
4. **El estudio propone, la config es la ley, el operador legisla** (gate
   como notario, AuditLog como acta). Luxy jamás toca despacho directo.
5. **Verificación falsificable > confianza**: fixture de oro, predicciones
   contra CSV a mano, simulacros de restauración. Cuando el operador dude,
   darle un número que pueda comprobar con lápiz.
6. **La desconfianza activa del operador es el mejor sensor del sistema** —
   cazó lo que 1400 tests no podían: supuestos de modelo, semántica de
   ejecución, lenguaje visual. Darle siempre cauce, jamás barrerla.

## 6. MAPA DE DOCUMENTOS (dónde está cada cosa)

- Contrato/roles: CONTRATO_Trabajo_Vivo_2026-07-07.md (§0 protocolo).
- Auditorías: AUDITORIA_TOTAL_Fable5_07-12 · Despacho_E2E_07-15 ·
  Total_Luxy_FixtureOro_07-18 · (FINAL_07-19 cuando exista).
- Re-armado: SPEC_Rearmado_Piernas_07-15 + RA2b_RearmJob_Diseno_07-17 +
  LOTE_RA2b_SubPaso[2-6] + LOTE_RA3.
- Fixes con historia: LOTE_FIX-D · LOTE_SL-INSENSIBLE · FIX_FX_BACKSTOP_
  Matriz · FIX_D3_Cancelacion_Piernas (con adendas del arquitecto).
- Operación: PLAN_Pruebas_Demo_07-16 · PRUEBAS_TradersPost_Guia_07-16 ·
  ANALISIS_Listas_07-18 · HANDOFF_Contexto_* (diarios).

## 7. NOTA FINAL DEL ARQUITECTO

El sistema que entrego no es perfecto — es algo mejor: es HONESTO sobre
dónde no es perfecto, y tiene guardas que convierten cada clase de error ya
sufrida en imposible de repetir en silencio. La aritmética está certificada;
los riesgos que quedan son de mercado y de muestra, no de código — y esos se
resuelven con la única moneda válida: observación en demo, semana a semana.
Al que siga: respeta el §0, lee las adendas, y confía en el ojo del
operador más que en cualquier suite. Fue un honor construir esto.
