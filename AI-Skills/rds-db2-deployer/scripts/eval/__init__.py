"""End-to-end evaluation harness for the rds-db2-provision-skill (task 15).

This package adapts the sibling ``rds-db2`` skill's manipulation-eval pattern
(``evals/rds-db2/scripts/create_db2_instance_01.py`` — a ``setup`` /
``validate`` / ``rollback`` triple driven against a burner account) to the
**composer** skill: instead of imperatively calling the RDS API, the eval drives
the skill's own local pipeline (resolve -> validate -> render Terraform) and then
applies the rendered Terraform against a burner account, asserting the created
resources match the resolved intent, and finally destroying everything with
cleanup verification.

Layout (mirrors the manipulation-eval split so each piece is independently
runnable):

* :mod:`scripts.eval.baseline_pipeline` — the pure, AWS-mutation-free pipeline
  driver. Builds the baseline "Deploy RDS for Db2 instance" (sandbox)
  ``Deployment_Intent`` through the real resolvers, validates it, and renders the
  Terraform. Uses the live boto3 engine-version lister for R5.1 truth-grounding
  but performs **no** mutating AWS call. This is the part the always-on pytest
  (:mod:`scripts.tests.test_eval_baseline`) exercises (R3.4 field set + R10.8
  ``terraform validate``).

* :mod:`scripts.eval.live_baseline` — the burner-account driver. ``setup`` /
  ``deploy`` / ``validate`` / ``rollback`` that stage the rendered modules into a
  scratch dir, ``terraform apply`` them in dependency order against the burner,
  confirm the RDS for Db2 instance (and supporting resources) created, and then
  ``terraform destroy`` + describe-call cleanup verification. Cleanup is
  mandatory and runs even when apply fails partway (task 15.1).
"""
