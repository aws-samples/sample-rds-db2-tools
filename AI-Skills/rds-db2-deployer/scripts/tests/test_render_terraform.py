"""Unit tests for the Terraform_Composer mapping + tfvars rendering (task 7.1).

These tests cover Requirement 10.1-10.4:

* R10.1 - the rendered root module references the existing modules by relative
  ``source`` path and is not a parallel imperative deployer.
* R10.2 - a ``terraform.tfvars`` is emitted per enabled module.
* R10.3 - intent fields map to the modules' REAL variable names (verified
  against the actual ``variables.tf`` files); the edition-string-per-module and
  list->scalar transforms produce the correct values.
* R10.4 - an intent field with no mapping entry halts rendering and is reported
  by name; no fabricated variable name is ever emitted.

They run without Terraform or AWS: rendering is pure text generation, and the
module variables are parsed from the on-disk ``variables.tf`` files.
"""

from __future__ import annotations

import copy

import pytest

from scripts.render_terraform import (
    INTENT_FIELD_MAPPING,
    FabricatedVariableError,
    UnmappedIntentFieldError,
    VarTarget,
    collect_module_variables,
    format_hcl_value,
    load_module_variable_index,
    parse_module_variables,
    render_terraform,
    render_tfvars,
)


# ---------------------------------------------------------------------------
# A representative VALID, resolved Deployment_Intent (matches the schema's
# always-required set and the design's illustrative document).
# ---------------------------------------------------------------------------


def _base_intent() -> dict:
    """A prod, large, db2-se intent that satisfies the schema's required set."""
    return {
        "schema_version": "1.0",
        "deployment_tier": "prod",
        "workload_size": "large",
        "region": "us-east-1",
        "engine": "db2-se",
        "engine_version": "12.1.4.0",
        "master_username": "admin",
        "manage_master_user_password": True,
        "master_user_secret_kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-secret",
        "db_name": "DB2DB",
        "port": 8392,
        "license_model": "bring-your-own-license",
        "instance_class": "db.r7i.4xlarge",
        "allocated_storage": 16000,
        "storage_type": "io2",
        "iops": 16000,
        "multi_az": True,
        "backup_retention_period": 7,
        "publicly_accessible": False,
        "storage_encrypted": True,
        "kms_key_id": "arn:aws:kms:us-east-1:111122223333:key/mrk-1234",
        "vpc_security_group_ids": ["sg-0123456789abcdef0"],
        "vpc_id": "vpc-0123456789abcdef0",
        "db_subnet_group_name": "rds-db2-prod-subnets",
        "db_parameter_group_name": "",
        "monitoring_interval": 15,
        "monitoring_role_arn": "arn:aws:iam::111122223333:role/rds-db2-monitoring",
        "enable_cloudwatch_logs_exports": ["diag.log", "notify.log"],
        "deletion_protection": True,
        "ibm_customer_id": "1234567",
        "ibm_site_id": "1234567890",
        "db_instance_identifier": "",
        "tags": {"Project": "ACME", "Environment": "prod", "Owner": "db-team"},
        "db_parameter_group_family": "db2-se-12.1",
        "_provenance": {"engine": "user_provided"},
    }


@pytest.fixture
def intent() -> dict:
    return _base_intent()


# ---------------------------------------------------------------------------
# Mapping-table grounding: every target is a real module variable (R10.3).
# ---------------------------------------------------------------------------


def test_every_mapping_target_is_a_real_module_variable(terraform_modules_root):
    """R10.3: no mapping target may name a variable absent from variables.tf."""
    index = load_module_variable_index(terraform_modules_root)
    for field, mapping in INTENT_FIELD_MAPPING.items():
        if isinstance(mapping, str):
            continue
        for target in mapping:
            declared = index.get(target.module, set())
            assert target.variable in declared, (
                f"mapping for '{field}' targets {target.module}:{target.variable} "
                "which is not declared in that module's variables.tf"
            )


def test_parse_module_variables_reads_real_names(terraform_modules_root):
    """The parser picks up known 5-rds variables and not fabricated ones."""
    rds_vars = parse_module_variables(terraform_modules_root / "5-rds")
    assert {"engine", "instance_class", "allocated_storage", "db2_port"} <= rds_vars
    assert "nonexistent_variable" not in rds_vars


# ---------------------------------------------------------------------------
# Correct mapping of names + values (R10.3).
# ---------------------------------------------------------------------------


def test_core_fields_map_to_correct_5rds_variables(intent, terraform_modules_root):
    modules = collect_module_variables(
        intent, variable_index=load_module_variable_index(terraform_modules_root)
    )
    rds = modules["5-rds"].variables
    assert rds["engine"] == "db2-se"
    assert rds["engine_version"] == "12.1.4.0"
    assert rds["engine_major_version"] == "12.1"  # derived major
    assert rds["instance_class"] == "db.r7i.4xlarge"
    assert rds["allocated_storage"] == 16000
    assert rds["storage_type"] == "io2"
    assert rds["iops"] == 16000
    assert rds["multi_az"] is True
    assert rds["db_name"] == "DB2DB"
    assert rds["backup_retention_period"] == 7
    assert rds["deletion_protection"] is True
    # port -> db2_port (renamed variable)
    assert rds["db2_port"] == 8392
    # kms_key_id -> kms_key_arn (renamed variable)
    assert rds["kms_key_arn"] == "arn:aws:kms:us-east-1:111122223333:key/mrk-1234"
    # db_parameter_group_name -> parameter_group_name (renamed variable)
    assert rds["parameter_group_name"] == ""
    # vpc_security_group_ids (list) -> security_group_id (scalar, first element)
    assert rds["security_group_id"] == "sg-0123456789abcdef0"
    # workload_size -> db_size_label (abbreviated)
    assert rds["db_size_label"] == "l"


def test_edition_string_differs_per_module(intent, terraform_modules_root):
    """R10.3 / design note: the edition string differs per module."""
    modules = collect_module_variables(
        intent, variable_index=load_module_variable_index(terraform_modules_root)
    )
    assert modules["5-rds"].variables["engine"] == "db2-se"
    assert modules["4-parameter-group"].variables["engine_edition"] == "se"
    assert modules["6-license-manager"].variables["db2_edition"] == "SE"


def test_ibm_ids_map_to_parameter_group_and_are_sensitive(intent, terraform_modules_root):
    modules = collect_module_variables(
        intent, variable_index=load_module_variable_index(terraform_modules_root)
    )
    pg = modules["4-parameter-group"]
    assert pg.variables["ibm_customer_id"] == "1234567"
    assert pg.variables["ibm_site_id"] == "1234567890"
    assert {"ibm_customer_id", "ibm_site_id"} <= pg.sensitive_variables


def test_tags_expand_to_default_tags_variables(intent, terraform_modules_root):
    """tags -> provider default_tags: Project->tag, Owner->owner, Environment via tier."""
    modules = collect_module_variables(
        intent, variable_index=load_module_variable_index(terraform_modules_root)
    )
    rds = modules["5-rds"].variables
    assert rds["tag"] == "ACME"
    assert rds["owner"] == "db-team"
    assert rds["environment"] == "prod"  # from deployment_tier
    assert rds["aws_region"] == "us-east-1"


# ---------------------------------------------------------------------------
# Unmapped field halts + reports by name (R10.4).
# ---------------------------------------------------------------------------


def test_unmapped_field_raises_and_names_field(intent, terraform_modules_root):
    intent["totally_unknown_field"] = "boom"
    with pytest.raises(UnmappedIntentFieldError) as exc:
        collect_module_variables(
            intent, variable_index=load_module_variable_index(terraform_modules_root)
        )
    assert exc.value.field == "totally_unknown_field"
    assert "totally_unknown_field" in str(exc.value)


def test_render_terraform_halts_on_unmapped_field(intent, terraform_modules_root):
    """R10.4: full render path halts and emits no files for an unmapped field."""
    intent["surprise_param"] = 42
    with pytest.raises(UnmappedIntentFieldError):
        render_terraform(intent, modules_root=terraform_modules_root)


def test_underscored_metadata_is_not_treated_as_unmapped(intent, terraform_modules_root):
    """Resolver-internal underscored keys never trigger the unmapped halt."""
    intent["_edition_conversion"] = {"from": "db2-se", "to": "db2-ae"}
    intent["_superseded_tier_defaults"] = {"instance_class": "db.t3.xlarge"}
    # Should not raise.
    collect_module_variables(
        intent, variable_index=load_module_variable_index(terraform_modules_root)
    )


# ---------------------------------------------------------------------------
# Never fabricate a variable name (R10.3/10.4).
# ---------------------------------------------------------------------------


def test_fabricated_variable_target_raises(monkeypatch, intent, terraform_modules_root):
    """A mapping target naming a non-existent variable halts rendering."""
    patched = copy.deepcopy(INTENT_FIELD_MAPPING)
    patched["db_name"] = [VarTarget("5-rds", "this_var_does_not_exist")]
    monkeypatch.setattr("scripts.render_terraform.INTENT_FIELD_MAPPING", patched)
    with pytest.raises(FabricatedVariableError) as exc:
        render_terraform(intent, modules_root=terraform_modules_root)
    assert exc.value.variable == "this_var_does_not_exist"
    assert exc.value.module == "5-rds"


# ---------------------------------------------------------------------------
# Full render: root module + per-module tfvars (R10.1, R10.2).
# ---------------------------------------------------------------------------


def test_render_emits_root_module_and_core_tfvars(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    # R10.2: a tfvars per enabled module, and the core modules are always there.
    assert "main.tf" in result.files
    assert "4-parameter-group/terraform.tfvars" in result.files
    assert "5-rds/terraform.tfvars" in result.files
    for module in result.enabled_modules:
        assert f"{module}/terraform.tfvars" in result.files


def test_root_module_references_modules_by_pinned_git_source(intent, terraform_modules_root):
    """#1: by default the root references the reused modules by a PINNED git ref
    (the source of truth), never a relative local path."""
    result = render_terraform(intent, modules_root=terraform_modules_root)
    main_tf = result.files["main.tf"]
    assert 'module "rds"' in main_tf
    assert 'module "parameter_group"' in main_tf
    # Pinned git source of the form git::<repo>//<subdir>/<module>?ref=<tag>.
    assert (
        "git::https://github.com/aws-samples/sample-rds-db2-tools.git"
        "//tools/rds-db2-terraform/5-rds?ref=" in main_tf
    )
    assert "source =" in main_tf
    # The default must be a git source, not a relative local path.
    assert "../../../RDS-Db2-Terraform/5-rds" not in main_tf
    # Not an imperative deployer: it only declares module blocks + providers.
    assert "aws_db_instance" not in main_tf


def test_root_module_local_source_mode_is_airgap_fallback(intent, terraform_modules_root):
    """#1 airgap fallback: source_mode='local' emits relative vendored paths and
    no git source."""
    result = render_terraform(
        intent, modules_root=terraform_modules_root, source_mode="local"
    )
    main_tf = result.files["main.tf"]
    # A relative local path to the modules dir (the exact prefix depends on the
    # layout: dev sibling RDS-Db2-Terraform vs published tools/rds-db2-terraform).
    modules_dir_name = terraform_modules_root.name
    assert f"{modules_dir_name}/5-rds" in main_tf
    assert "source = \"../" in main_tf
    assert "git::" not in main_tf


def test_root_module_git_ref_is_overridable(intent, terraform_modules_root):
    """#1: the pinned ref is configurable so a release tag can be selected."""
    result = render_terraform(
        intent, modules_root=terraform_modules_root, module_ref="v1.2.3"
    )
    main_tf = result.files["main.tf"]
    assert "?ref=v1.2.3" in main_tf
    # Every enabled module is pinned to the same ref (0-backend-setup is NOT a
    # child module of the deployment root — it is consumed via the backend block).
    assert main_tf.count("?ref=v1.2.3") >= len(result.enabled_modules)


def test_unknown_source_mode_raises(intent, terraform_modules_root):
    from scripts.render_terraform import RenderingError

    with pytest.raises(RenderingError):
        render_terraform(
            intent, modules_root=terraform_modules_root, source_mode="registry"
        )


# ---------------------------------------------------------------------------
# #2 — per-deployment S3 remote state backend.
# ---------------------------------------------------------------------------


def test_backend_block_uses_per_deployment_state_key(intent, terraform_modules_root):
    """#2: the rendered root carries a backend "s3" block whose key is unique to
    this deployment (derived from db_instance_identifier)."""
    intent["db_instance_identifier"] = "rds-db2-prod-large-acme"
    result = render_terraform(intent, modules_root=terraform_modules_root)
    main_tf = result.files["main.tf"]
    assert 'backend "s3"' in main_tf
    assert 'key            = "rds-db2/rds-db2-prod-large-acme/terraform.tfstate"' in main_tf
    assert 'region         = "us-east-1"' in main_tf
    assert "dynamodb_table" in main_tf


def test_distinct_deployments_get_distinct_state_keys(intent, terraform_modules_root):
    """#2: two deployments never share a state key, so N instances don't collide."""
    a = copy.deepcopy(intent)
    a["db_instance_identifier"] = "rds-db2-prod-one"
    b = copy.deepcopy(intent)
    b["db_instance_identifier"] = "rds-db2-prod-two"
    key_a = render_terraform(a, modules_root=terraform_modules_root).files["main.tf"]
    key_b = render_terraform(b, modules_root=terraform_modules_root).files["main.tf"]
    assert "rds-db2/rds-db2-prod-one/terraform.tfstate" in key_a
    assert "rds-db2/rds-db2-prod-two/terraform.tfstate" in key_b
    assert "rds-db2-prod-two" not in key_a
    assert "rds-db2-prod-one" not in key_b


def test_emit_backend_false_omits_backend_block(intent, terraform_modules_root):
    """#2: emit_backend=False (validate harnesses) renders no backend block."""
    result = render_terraform(
        intent, modules_root=terraform_modules_root, emit_backend=False
    )
    assert 'backend "s3"' not in result.files["main.tf"]


def test_explicit_backend_config_overrides_derived(intent, terraform_modules_root):
    """#2: an explicit BackendConfig wins over the per-deployment derivation."""
    from scripts.render_terraform import BackendConfig

    backend = BackendConfig(
        bucket="my-real-state-bucket",
        region="eu-west-1",
        key="custom/path/terraform.tfstate",
        lock_table="my-lock-table",
    )
    result = render_terraform(
        intent, modules_root=terraform_modules_root, backend=backend
    )
    main_tf = result.files["main.tf"]
    assert 'bucket         = "my-real-state-bucket"' in main_tf
    assert 'key            = "custom/path/terraform.tfstate"' in main_tf
    assert 'dynamodb_table = "my-lock-table"' in main_tf


def test_state_key_helper_is_well_formed_for_empty_name():
    from scripts.render_terraform import BackendConfig

    assert BackendConfig.state_key_for("") == "rds-db2/unnamed/terraform.tfstate"
    assert (
        BackendConfig.state_key_for("inst-1") == "rds-db2/inst-1/terraform.tfstate"
    )


def test_backend_setup_not_a_child_module_of_deployment_root(intent, terraform_modules_root):
    """The deployment root CONSUMES the remote-state backend (the backend "s3"
    block); it must NOT reference 0-backend-setup as a child module (that would
    try to re-create the state bucket and needs a var the intent lacks)."""
    result = render_terraform(intent, modules_root=terraform_modules_root)
    assert 'module "backend_setup"' not in result.files["main.tf"]
    # It is the bootstrap, never an intent-driven module nor a rendered tfvars.
    assert "0-backend-setup" not in result.enabled_modules
    assert "0-backend-setup/terraform.tfvars" not in result.files
    # The backend it bootstraps is still consumed via the backend "s3" block.
    assert 'backend "s3"' in result.files["main.tf"]


def test_rendered_tfvars_uses_real_variable_names_only(intent, terraform_modules_root):
    """R10.3: every assignment in a rendered tfvars is a real module variable."""
    result = render_terraform(intent, modules_root=terraform_modules_root)
    index = load_module_variable_index(terraform_modules_root)
    for module, rendered in result.modules.items():
        declared = index[module]
        for name in rendered.variables:
            assert name in declared, f"{module}: {name} is not a real variable"


def test_rendered_tfvars_text_is_valid_assignments(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    text = result.files["5-rds/terraform.tfvars"]
    assert 'engine = "db2-se"' in text
    assert "multi_az = true" in text
    assert "allocated_storage = 16000" in text
    assert "db2_port = 8392" in text


def test_sensitive_values_annotated_in_tfvars(intent, terraform_modules_root):
    result = render_terraform(intent, modules_root=terraform_modules_root)
    pg_text = result.files["4-parameter-group/terraform.tfvars"]
    assert "ibm_customer_id" in pg_text
    assert "# sensitive" in pg_text


def test_render_is_deterministic(intent, terraform_modules_root):
    first = render_terraform(intent, modules_root=terraform_modules_root)
    second = render_terraform(copy.deepcopy(intent), modules_root=terraform_modules_root)
    assert first.files == second.files


# ---------------------------------------------------------------------------
# HCL value formatting.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, "true"),
        (False, "false"),
        (8392, "8392"),
        ("db2-se", '"db2-se"'),
        (["a", "b"], '["a", "b"]'),
        ([], "[]"),
    ],
)
def test_format_hcl_value(value, expected):
    assert format_hcl_value(value) == expected


def test_format_hcl_value_escapes_quotes():
    assert format_hcl_value('a"b') == '"a\\"b"'


def test_render_tfvars_sorts_variables():
    from scripts.render_terraform import RenderedModule

    rendered = RenderedModule(
        module="5-rds", variables={"zeta": 1, "alpha": 2}, sensitive_variables=set()
    )
    text = render_tfvars(rendered)
    assert text.index("alpha = 2") < text.index("zeta = 1")
