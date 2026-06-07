"""Offline evidence-report generator for the task-15.2 capability scenarios.

Runs the AWS-free resolve -> validate -> render pipeline for every scenario with
a RECORDED grounded engine-version lister (no AWS), and writes a masked
per-scenario evidence artifact via the shared artifacts writer. This produces a
durable record of the resolver edition decisions (Group A) and the rendered
capability evidence (Group B) independent of whether a live burner apply was
possible — the local pipeline is the authoritative source for the rendering +
edition reconciliation the task exercises.
"""

from __future__ import annotations

from scripts.eval.capabilities_pipeline import GROUP_A_BUILDERS, GROUP_B_BUILDERS
from scripts.artifacts import write_artifacts, STATUS_COMPLETED

# Grounded snapshot (captured from the burner before it was reclaimed); the
# highest 12.1 minor is a REAL value the RDS API returned (R5.1), never invented.
RECORDED = {
    "db2-se": ["11.5.9.0.sb00075854.r1", "12.1.4.0.sb00080714.r1"],
    "db2-ae": ["11.5.9.0.sb00075854.r1", "12.1.4.0.sb00080714.r1"],
}


def _lister(engine: str, region: str):
    return list(RECORDED.get(engine, []))


def main() -> int:
    scenarios = {}
    a_intent = {}
    for builder in (*GROUP_A_BUILDERS, *GROUP_B_BUILDERS):
        sc = builder(lister=_lister)
        if sc.name == "se_to_ae_oversized":
            a_intent = sc.intent
        rds = sc.render.modules["5-rds"].variables if sc.render else {}
        scenarios[sc.name] = {
            "group": sc.group,
            "proof_intended_on_burner": sc.proof,
            "validation_ok": sc.validation.ok,
            "engine": sc.intent.get("engine"),
            "instance_class": sc.intent.get("instance_class"),
            "db_parameter_group_family": sc.intent.get("db_parameter_group_family"),
            "edition_conversion": sc.edition_conversion,
            "downgrade_guidance_present": bool(sc.downgrade_guidance),
            "enabled_modules": list(sc.render.enabled_modules) if sc.render else [],
            "evidence": sc.notes,
        }

    res = write_artifacts(
        "eval-capabilities-15-2",
        intent=a_intent,
        status=STATUS_COMPLETED,
        plan_summary={
            "note": (
                "Local resolve/validate/render evidence (AWS-free). Live burner "
                "apply proofs were blocked: the burner account was reclaimed "
                "(ada refresh -> AWSAccountNotFoundException) after creds expired "
                "mid-run; nothing was applied (no tfstate), so there were no "
                "leftovers to clean."
            ),
            "scenarios": scenarios,
        },
    )
    print(f"wrote evidence artifact to {res.directory}")
    for name, e in scenarios.items():
        print(f"  {name}: engine={e['engine']} family={e['db_parameter_group_family']} "
              f"validation_ok={e['validation_ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
