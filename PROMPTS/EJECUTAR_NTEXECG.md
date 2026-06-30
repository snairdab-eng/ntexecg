# EJECUTAR — NTEXECG (instrucciones para Claude Code)

**Raíz de trabajo del proyecto:** `c:\NTEXECG`
**Carpeta de prompts e inputs:** `c:\NTEXECG\PROMPTS`

---

## 1. Lee primero (todo desde `c:\NTEXECG\PROMPTS`)

- `NTEXECG_build_prompt.md` → especificación completa de construcción. **Es la fuente de verdad del comportamiento.**
- `instrument_catalog.json`
- `strategy_profile.schema.json`
- `account_profile.schema.json`
- `strategy_profile.example.MES.json`

Los 4 JSON son la fuente de verdad del **modelo de datos**.

## 2. Tarea

1. Lee el build prompt y los 4 archivos JSON.
2. Construye el proyecto en `c:\NTEXECG` exactamente según ese spec (stack, pipeline de validación, reglas innegociables y criterios de aceptación que ahí se definen).
3. Corre la suite de pruebas (`pytest`) y deja todo funcional.

## 3. Reporte de salida (obligatorio)

Al terminar, genera el reporte en:

```
c:\NTEXECG\PROMPTS\REPORTE_SALIDA.md
```

Con esta estructura:
- **Resumen** de lo construido (módulos/archivos creados).
- **Criterios de aceptación**: estado de los 10 (cuáles pasan / fallan).
- **Pytest**: resumen de la corrida.
- **Decisiones de implementación** que tomaste y por qué.
- **Ambigüedades / supuestos**: cualquier punto del spec que no estaba claro y cómo lo resolviste. **No adivines en silencio: déjalo explícito aquí.**
- **Pendientes / próximos pasos.**
- **Cómo correr** el proyecto y cómo dar de alta una estrategia.

Si `REPORTE_SALIDA.md` ya existe, **añade una sección nueva fechada** en vez de borrar lo anterior.

## 4. Reglas

- No salgas del alcance del spec: **no** reimplementes TradersPost, **no** reintentes reenvíos, **no** asumas defaults silenciosos.
- Si algo te bloquea, **no te detengas sin dejar rastro**: documenta el bloqueo en el reporte y continúa con lo que sí puedas avanzar.
- Manténlo simple, legible y auditable: el valor de NTEXECG está en que las reglas sean obvias.
