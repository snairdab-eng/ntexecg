# scripts/archivo — one-shots históricos (era Anexo 21 / política v1)

Archivados en el lote P3 de la auditoría Fase A (2026-07-06) — NO borrados:
son la evidencia reproducible de decisiones ya aplicadas, pero no forman
parte del flujo vivo (el Motor de Riesgo y las pestañas los superseden).

Se corren igual que antes, con el prefijo del paquete:
`python -m scripts.archivo.<nombre>`.

| script | qué hizo |
|---|---|
| `apply_anexo21_demo.py` | aplicación demo de los filtros de calidad (Anexo 21) |
| `apply_profile_policy_v1.py` | política operativa v1 sobre los perfiles |
| `revert_asset_profiles_v1.py` | rollback de la política v1 |
| `diag_profiles.py` | diagnóstico puntual de perfiles |
| `compare_filter_decisions.py` | comparación de decisiones con/sin filtros |
| `eval_quality_filters.py` | evaluación de los filtros de calidad |
