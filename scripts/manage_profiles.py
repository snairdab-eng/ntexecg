#!/usr/bin/env python3
"""manage_profiles — gestiona los PERFILES DE RIESGO (tiers) de una estrategia.

Los perfiles viven en pipeline_config_json["profiles"] y son DELTAS sobre la base
(el perfil actual). Cada perfil hereda todo y sólo overridea lo que necesita —
típicamente las cantidades por pierna. El dispatch envía un juego de piernas por
perfil habilitado a su propio webhook de TradersPost.

Sólo lectura por defecto en --show/--preview. --set-json escribe (dry-run salvo
--apply); backup JSON + auditoría.

Uso:
  # Ver perfiles y destinos resueltos (read-only)
  python -m scripts.manage_profiles --strategy ES5m_ConfNormal_TC_TSR --show

  # Previsualizar un archivo de perfiles SIN escribir
  python -m scripts.manage_profiles --strategy ES5m_ConfNormal_TC_TSR --set-json perfiles.json --preview

  # Escribir (requiere --apply)
  python -m scripts.manage_profiles --strategy ES5m_ConfNormal_TC_TSR --set-json perfiles.json --apply

Formato de perfiles.json (lista). Campos opcionales se HEREDAN de la base:
  [
    {"name":"agresivo",    "enabled":true,  "webhook_url":"https://...AGR"},
    {"name":"moderado",    "enabled":true,  "webhook_url":"https://...MOD", "quantities":[0,2,1], "max_contracts":2},
    {"name":"conservador", "enabled":true,  "webhook_url":"https://...APEX","quantities":[0,1,0], "max_contracts":1, "note":"APEX 50k"},
    {"name":"sin_riesgo",  "enabled":false, "webhook_url":"https://...X",   "dry_run":true}
  ]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services import dispatch_profiles as dprof
from app.services.audit_service import AuditService
from app.services.config_resolver import ConfigResolver


def _print_destinations(config: dict) -> None:
    dests = dprof.resolve_destinations(config)
    print(f"\n  Destinos resueltos ({len(dests)}):")
    for d in dests:
        se = d["scale_entry"] or {}
        name = d["name"] or "(base)"
        wh = (d["webhook_url"] or "—")
        wh_tail = wh.split("/")[-1] if "/" in wh else wh
        gate = "LIVE" if (d["traderspost_enabled"] and not d["dry_run"]) else "dry-run"
        print(f"   • {name:<14} qty={se.get('quantities')} levels={se.get('levels')} "
              f"sl={d['sl_atr_multiplier']} tp={d['tp_atr_multiplier']} "
              f"wh=…{wh_tail} [{gate}] tag={dprof.delivery_tag(d['name'])}")


def _validate(profiles) -> list[str]:
    errs: list[str] = []
    if not isinstance(profiles, list):
        return ["el JSON debe ser una LISTA de perfiles"]
    if len(profiles) > 8:
        errs.append("demasiados perfiles (máx 8)")
    names = set()
    for i, p in enumerate(profiles):
        if not isinstance(p, dict):
            errs.append(f"[{i}] no es un objeto"); continue
        nm = p.get("name")
        if not nm:
            errs.append(f"[{i}] falta 'name'")
        elif nm in names:
            errs.append(f"[{i}] nombre duplicado: {nm}")
        else:
            names.add(nm)
        if p.get("enabled") and not (p.get("webhook_url")):
            # heredará el webhook base si falta; sólo avisamos
            errs.append(f"[{i}] {nm}: habilitado sin webhook_url propio (heredará el de la base)")
        q = p.get("quantities")
        if q is not None and (not isinstance(q, list) or any(not isinstance(x, int) for x in q)):
            errs.append(f"[{i}] {nm}: 'quantities' debe ser lista de enteros")
        mc = p.get("max_contracts")
        if mc is not None and (not isinstance(mc, int) or mc < 0):
            errs.append(f"[{i}] {nm}: 'max_contracts' debe ser entero >= 0")
    return errs


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, help="strategy_id exacto")
    ap.add_argument("--show", action="store_true", help="muestra perfiles actuales + destinos (read-only)")
    ap.add_argument("--set-json", help="archivo JSON con la lista de perfiles a escribir")
    ap.add_argument("--preview", action="store_true", help="con --set-json: muestra el resultado sin escribir")
    ap.add_argument("--apply", action="store_true", help="aplica los cambios (si no, dry-run)")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        strat = (await db.execute(
            select(Strategy).where(Strategy.strategy_id == args.strategy)
        )).scalar_one_or_none()
        if strat is None:
            print(f"❌ No existe la estrategia {args.strategy}"); return
        prof = (await db.execute(
            select(StrategyProfile).where(StrategyProfile.strategy_id == args.strategy)
        )).scalar_one_or_none()
        if prof is None:
            print(f"❌ {args.strategy} no tiene StrategyProfile"); return

        config = await ConfigResolver().resolve(db, args.strategy, strat.asset_symbol)
        current = (prof.pipeline_config_json or {}).get("profiles") or []

        print(f"=== {args.strategy} ===")
        print(f"Base: scale_entry.quantities={(config.get('scale_entry') or {}).get('quantities')} "
              f"levels={(config.get('scale_entry') or {}).get('levels')} "
              f"sl={config.get('sl_atr_multiplier')} tp={config.get('tp_atr_multiplier')} "
              f"webhook=…{(config.get('traderspost_webhook_url') or '—').split('/')[-1]}")
        print(f"Perfiles actuales ({len(current)}): "
              f"{[ (p.get('name'), 'on' if p.get('enabled') else 'off') for p in current ]}")

        # SHOW (read-only): destinos con la config actual
        if args.show or not args.set_json:
            _print_destinations(config)
            if not args.set_json:
                return

        # SET-JSON: cargar, validar, previsualizar, (aplicar)
        new_profiles = json.loads(Path(args.set_json).read_text(encoding="utf-8"))
        errs = _validate(new_profiles)
        warns = [e for e in errs if "heredará" in e]
        hard = [e for e in errs if "heredará" not in e]
        for w in warns:
            print(f"  ⚠ {w}")
        if hard:
            print("\n❌ Errores en el JSON:")
            for e in hard:
                print(f"   - {e}")
            return

        preview_cfg = dict(config)
        preview_cfg["profiles"] = new_profiles
        print("\n── Resultado propuesto ──")
        _print_destinations(preview_cfg)

        if args.preview or not args.apply:
            await db.rollback()
            print("\nℹ️  DRY-RUN / preview: sin cambios. Usa --apply para escribir.")
            return

        # Backup + escribir
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        Path("REPORTES").mkdir(exist_ok=True)
        bp = Path("REPORTES") / f"profiles_backup_{args.strategy}_{ts}.json"
        bp.write_text(json.dumps({"strategy_id": args.strategy, "profiles": current,
                                  "version": prof.version}, indent=2, default=str),
                      encoding="utf-8")
        pj = dict(prof.pipeline_config_json or {})
        pj["profiles"] = new_profiles
        prof.pipeline_config_json = pj
        prof.version = (prof.version or 1) + 1
        prof.updated_by = "manage_profiles"
        await AuditService().log(
            db, actor="manage_profiles", action="UPDATE",
            object_type="StrategyProfile", object_id=args.strategy,
            old_value={"profiles": current}, new_value={"profiles": new_profiles},
            reason="set risk profiles",
        )
        await db.commit()
        print(f"\n🗄️  Backup → {bp}")
        print("✅ Perfiles aplicados.")


if __name__ == "__main__":
    asyncio.run(main())
