"""
Shared helpers for the Strands tutorial.

The most important thing here is build_model(): a single place that decides
which model provider every lab uses. This is itself a lesson from the article
series — the agent code never changes; only this model choice does. That is the
"model-agnostic" claim, made concrete.

Toggle providers with an environment variable:
    export USE_OLLAMA=1     # fully local, free (needs `ollama serve` + a pulled model)
    (unset / 0 / false)     # default: Amazon Bedrock (matches the articles)

NOTE: USE_OLLAMA is read by VALUE, not just presence. "1", "true", "yes", "on"
(any case) select Ollama; anything else — including "0", "false", "" or unset —
selects Bedrock. So `export USE_OLLAMA=0` correctly means Bedrock.

BEDROCK MODEL SELECTION: model IDs go end-of-life over time, so rather than
hardcoding one, build_model() discovers the newest ACTIVE Claude model from
Bedrock at runtime (see resolve_bedrock_model_id). Pin a specific one anytime:
    export BEDROCK_MODEL_ID=us.anthropic.claude-3-5-sonnet-20241022-v2:0
"""

import os

# Values that mean "yes, use Ollama". Everything else (incl. "0", "", unset) = Bedrock.
_TRUTHY = {"1", "true", "yes", "on"}


def use_ollama() -> bool:
    """Return True only if USE_OLLAMA is set to a truthy VALUE (not just present)."""
    return os.environ.get("USE_OLLAMA", "").strip().lower() in _TRUTHY


# A last-resort default if we can't reach Bedrock to discover models. Kept
# recent-ish, but the whole point of the resolver below is to not depend on it.
_FALLBACK_MODEL_ID = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

# Simple in-process cache so repeated build_model() calls in one run (e.g. the
# multi-agent lab) don't re-query Bedrock every time.
_RESOLVED_MODEL_CACHE: dict[str, str] = {}


# Family preference for a TUTORIAL: Sonnet is the balanced default (plenty
# capable, far cheaper/faster than Opus for repeated lab runs); Haiku next
# (cheapest/fastest); Opus last (most capable but pricey and slow). Override the
# whole thing with BEDROCK_MODEL_ID if you want a specific model.
_FAMILY_PREFERENCE = ("sonnet", "haiku", "opus")


def _family_rank(model_id: str) -> int:
    mid = model_id.lower()
    for i, fam in enumerate(_FAMILY_PREFERENCE):
        if fam in mid:
            return i
    return len(_FAMILY_PREFERENCE)  # unknown family -> lowest priority


def _recency_key(model_id: str) -> int:
    """Higher = newer. Handles BOTH dated ids (…-20250929-…) and undated newest
    ids (…-sonnet-5, …-opus-4-8) that have no date at all.

    Undated ids use the newest naming scheme, so they must rank ABOVE any dated
    id. Within undated ids we compare version as major.minor, with the MAJOR
    version dominating — so 'sonnet-5' (5.0) correctly beats 'sonnet-4-6' (4.6),
    which a naive digit concat would get backwards (5 < 46)."""
    import re

    m = re.search(r"(\d{8})", model_id)          # e.g. 20250929
    if m:
        return int(m.group(1))                    # dated: sort by the date itself

    # Undated: take up to major.minor, pad missing minor with 0, weight major high.
    nums = (re.findall(r"(\d+)", model_id) + ["0", "0"])[:2]  # e.g. ['5','0'], ['4','6']
    major, minor = int(nums[0]), int(nums[1])
    return 99_000_000 + major * 1000 + minor


def resolve_bedrock_model_id(region: str) -> str:
    """Discover a current, tutorial-appropriate Claude model on Bedrock at runtime.

    Why this exists: hardcoded model IDs reach end-of-life and then fail with
    ResourceNotFoundException ("this model version has reached the end of its
    life"). This tutorial is meant to work for a long time, so instead of pinning
    a version we ask Bedrock what's currently available and choose a good one.

    Strategy:
      1. List ACTIVE Anthropic Claude text models.
      2. Resolve each to an INVOCABLE id — most current Claude models are
         invoke-only through an inference profile (e.g. "us.anthropic…"), not the
         bare model id, so we map to a profile id when one exists.
      3. Rank by family preference (Sonnet > Haiku > Opus for a tutorial), then by
         recency (handles both dated and undated newest ids).
      4. On any failure (no creds, permissions, offline), fall back to a default
         and let the real call surface a clear, actionable error.

    Override anytime with:  export BEDROCK_MODEL_ID=<id>
    """
    if region in _RESOLVED_MODEL_CACHE:
        return _RESOLVED_MODEL_CACHE[region]

    resolved = _FALLBACK_MODEL_ID
    try:
        import boto3

        bedrock = boto3.client("bedrock", region_name=region)

        # 1) ACTIVE Claude text models (do NOT filter by inference type — the
        #    newest models are INFERENCE_PROFILE-only and would be dropped).
        resp = bedrock.list_foundation_models(byProvider="Anthropic")
        claude = [
            m for m in resp.get("modelSummaries", [])
            if m.get("modelLifecycle", {}).get("status") == "ACTIVE"
            and "TEXT" in (m.get("outputModalities") or [])
            and "claude" in m.get("modelId", "").lower()
        ]

        # 2) Map base model id -> inference-profile id. Prefer a regional "us."
        #    profile (broadly available) over "global." when both exist.
        profile_by_model: dict[str, str] = {}
        try:
            for page in bedrock.get_paginator("list_inference_profiles").paginate():
                for prof in page.get("inferenceProfileSummaries", []):
                    prof_id = prof.get("inferenceProfileId", "")
                    for mdl in prof.get("models", []):
                        base = (mdl.get("modelArn", "") or "").split("/")[-1]
                        if not base:
                            continue
                        cur = profile_by_model.get(base)
                        # Prefer us.* ; otherwise keep the first one seen.
                        if cur is None or (not cur.startswith("us.") and prof_id.startswith("us.")):
                            profile_by_model[base] = prof_id
        except Exception:
            pass  # profiles are best-effort; some models are invocable by base id

        def _invocable_id(model_summary: dict) -> str:
            base = model_summary.get("modelId", "")
            types = model_summary.get("inferenceTypesSupported") or []
            if "ON_DEMAND" in types:
                return base                       # bare id works
            return profile_by_model.get(base, "")  # else need a profile id

        # Keep only models we can actually invoke.
        candidates = [(m, _invocable_id(m)) for m in claude]
        candidates = [(m, inv) for (m, inv) in candidates if inv]

        if candidates:
            # Sort: family preference first (Sonnet>Haiku>Opus), then newest.
            best = min(
                candidates,
                key=lambda c: (
                    _family_rank(c[0].get("modelId", "")),
                    -_recency_key(c[0].get("modelId", "")),
                ),
            )
            resolved = best[1] or _FALLBACK_MODEL_ID
    except Exception:
        resolved = _FALLBACK_MODEL_ID

    _RESOLVED_MODEL_CACHE[region] = resolved
    return resolved


def build_model():
    """Return a Strands model object based on environment.

    NOTE ON API NAMES: Strands evolves. If an import below fails, run
        python3 -c "import strands.models as m; print(dir(m))"
    to see the exact class names your installed version exposes, and adjust.
    The *shape* (a model object you pass to Agent(model=...)) is stable.
    """
    if use_ollama():
        # ---- Option B: fully local via Ollama ----
        # The Ollama provider needs the `ollama` client package, which ships as an
        # optional extra of strands-agents. If it's missing, give a clear fix
        # instead of a bare "No module named 'ollama'".
        try:
            from strands.models.ollama import OllamaModel
        except ImportError as e:  # pragma: no cover - defensive
            # Could be the class import path OR the missing `ollama` dependency.
            try:
                from strands.models import OllamaModel  # type: ignore
            except ImportError:
                raise ImportError(
                    "Ollama support isn't installed. The Ollama model provider "
                    "needs the optional 'ollama' package. Install it with:\n\n"
                    "    pip install 'strands-agents[ollama]'\n\n"
                    "Then make sure Ollama itself is running:\n"
                    "    ollama serve            # in one shell\n"
                    "    ollama pull llama3.1:latest   # in another shell\n\n"
                    f"(original import error: {e})"
                ) from e

        return OllamaModel(
            host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model_id=os.environ.get("OLLAMA_MODEL", "llama3.1:latest"),
        )

    # ---- Option A (default): Amazon Bedrock ----
    # Requires: Bedrock model access enabled in your region + AWS creds set.
    try:
        from strands.models import BedrockModel
    except ImportError:  # pragma: no cover - defensive
        from strands.models.bedrock import BedrockModel  # type: ignore

    region = os.environ.get("AWS_REGION", "us-east-1")

    # Model IDs go end-of-life over time (a hardcoded one WILL eventually fail).
    # So we discover the newest ACTIVE Claude model from Bedrock at runtime,
    # unless you pin one explicitly with BEDROCK_MODEL_ID.
    model_id = os.environ.get("BEDROCK_MODEL_ID") or resolve_bedrock_model_id(region)

    return BedrockModel(model_id=model_id, region_name=region)


# ---------------------------------------------------------------------------
#  RDS for Db2 engine resolver (used by Lab 5)
# ---------------------------------------------------------------------------
# Fallback family if we can't reach RDS. The version is intentionally left
# unset in the fallback so create_db_instance uses the engine default.
_DB2_FALLBACK = {"engine_version": None, "param_group_family": "db2-ce-12.1"}
_DB2_ENGINE_CACHE: dict[str, dict] = {}


def resolve_db2_engine(region: str, engine: str = "db2-ce") -> dict:
    """Discover the newest RDS for Db2 engine version AND its parameter-group family.

    Why this exists: RDS engine versions are fully-qualified build strings like
    '12.1.4.0.sb00085812.r1' — you cannot pass a short '12.1.4'. Those builds also
    change over time, and each one belongs to a specific parameter-group family
    (e.g. 'db2-ce-12.1'). The instance's engine version and the parameter group's
    family MUST match, so we resolve both together from one API call and stay in
    sync automatically as new builds/families ship.

    Returns a dict: {"engine_version": <str>, "param_group_family": <str>}.
    On any failure (no creds/permissions/offline) returns a safe fallback whose
    engine_version is None — telling the caller to omit EngineVersion so RDS picks
    its default for the pinned family.

    Override anytime with env vars:
        export DB2_ENGINE_VERSION=12.1.4.0.sb00085812.r1
        export DB2_PARAM_GROUP_FAMILY=db2-ce-12.1
    """
    cache_key = f"{region}:{engine}"
    if cache_key in _DB2_ENGINE_CACHE:
        return _DB2_ENGINE_CACHE[cache_key]

    import re

    def _version_key(ver: str) -> tuple:
        # Sort by the numeric dotted head first (12.1.4.0), then the build number
        # embedded in the 'sbNNNNNNNN' segment, so the newest build wins.
        head = tuple(int(n) for n in re.findall(r"\d+", ver.split("sb")[0]))
        sb = re.search(r"sb(\d+)", ver)
        return (head, int(sb.group(1)) if sb else 0)

    result = dict(_DB2_FALLBACK)
    try:
        import boto3

        rds = boto3.client("rds", region_name=region)
        versions = []
        paginator = rds.get_paginator("describe_db_engine_versions")
        for page in paginator.paginate(Engine=engine):
            versions.extend(page.get("DBEngineVersions", []))

        if versions:
            newest = max(versions, key=lambda v: _version_key(v.get("EngineVersion", "")))
            result = {
                "engine_version": newest.get("EngineVersion"),
                "param_group_family": newest.get("DBParameterGroupFamily")
                or _DB2_FALLBACK["param_group_family"],
            }
    except Exception:
        result = dict(_DB2_FALLBACK)

    # Allow explicit env overrides to win over discovery.
    env_ver = os.environ.get("DB2_ENGINE_VERSION")
    env_fam = os.environ.get("DB2_PARAM_GROUP_FAMILY")
    if env_ver:
        result["engine_version"] = env_ver
    if env_fam:
        result["param_group_family"] = env_fam

    _DB2_ENGINE_CACHE[cache_key] = result
    return result


# ---------------------------------------------------------------------------
#  Networking resolver (used by Lab 5)
# ---------------------------------------------------------------------------
_DB2_NET_CACHE: dict[str, dict] = {}


def resolve_db2_networking(region: str) -> dict:
    """Discover a DB subnet group + security group to place the instance in.

    Why this exists: not every account has a *default VPC*. Accounts without one
    fail create_db_instance with "InvalidSubnet: No default subnet detected in
    VPC" unless you pass an explicit DBSubnetGroupName (and usually a security
    group). This finds sensible defaults so the lab works in both kinds of
    account, without you hand-copying subnet IDs.

    Returns {"db_subnet_group": <name or None>, "vpc_security_group_ids": [<id>...]}.
    Both may be empty/None — in an account WITH a default VPC, omitting them lets
    RDS use the default subnet + default SG, which is fine.

    Strategy:
      1. Pick a DB subnet group: prefer one named 'default*', else the first that
         spans >= 2 AZs (RDS requires two AZs), else the first available.
      2. Pick the 'default' security group of that subnet group's VPC.

    Override with env vars:
        export DB2_DB_SUBNET_GROUP=my-subnet-group
        export DB2_SECURITY_GROUP_IDS=sg-aaa,sg-bbb
    """
    if region in _DB2_NET_CACHE:
        return _DB2_NET_CACHE[region]

    result = {"db_subnet_group": None, "vpc_security_group_ids": []}
    try:
        import boto3

        rds = boto3.client("rds", region_name=region)
        ec2 = boto3.client("ec2", region_name=region)

        groups = rds.describe_db_subnet_groups().get("DBSubnetGroups", [])
        # Only usable groups: 'Complete' status and spanning at least 2 AZs.
        def _azs(g):
            return {sn["SubnetAvailabilityZone"]["Name"] for sn in g.get("Subnets", [])}

        usable = [g for g in groups if len(_azs(g)) >= 2]
        chosen = None
        for g in usable:
            if g.get("DBSubnetGroupName", "").lower().startswith("default"):
                chosen = g
                break
        if not chosen and usable:
            chosen = usable[0]
        elif not chosen and groups:
            chosen = groups[0]

        if chosen:
            result["db_subnet_group"] = chosen.get("DBSubnetGroupName")
            vpc_id = chosen.get("VpcId")
            if vpc_id:
                sgs = ec2.describe_security_groups(
                    Filters=[
                        {"Name": "vpc-id", "Values": [vpc_id]},
                        {"Name": "group-name", "Values": ["default"]},
                    ]
                ).get("SecurityGroups", [])
                if sgs:
                    result["vpc_security_group_ids"] = [sgs[0]["GroupId"]]
    except Exception:
        result = {"db_subnet_group": None, "vpc_security_group_ids": []}

    # Env overrides win.
    env_sng = os.environ.get("DB2_DB_SUBNET_GROUP")
    env_sgs = os.environ.get("DB2_SECURITY_GROUP_IDS")
    if env_sng:
        result["db_subnet_group"] = env_sng
    if env_sgs:
        result["vpc_security_group_ids"] = [s.strip() for s in env_sgs.split(",") if s.strip()]

    _DB2_NET_CACHE[region] = result
    return result


def suggest_subnet_group_command(region: str, group_name: str) -> str:
    """Build a ready-to-paste `aws rds create-db-subnet-group` command using two
    subnets from two different AZs in this account's VPC.

    Used by Lab 5 when no DB subnet group exists, so the user gets an exact fix
    instead of a raw InvalidSubnet error. Returns a helpful message even if we
    can't enumerate subnets (e.g. no permissions).
    """
    try:
        import boto3

        ec2 = boto3.client("ec2", region_name=region)
        subnets = ec2.describe_subnets().get("Subnets", [])
        # One subnet per AZ, need at least two AZs.
        by_az: dict[str, str] = {}
        for s in subnets:
            az = s.get("AvailabilityZone", "")
            if az and az not in by_az:
                by_az[az] = s.get("SubnetId", "")
        picks = list(by_az.values())[:2]
        if len(picks) >= 2:
            ids = " ".join(picks)
            return (
                f"aws rds create-db-subnet-group \\\n"
                f"    --db-subnet-group-name {group_name} \\\n"
                f"    --db-subnet-group-description 'Strands tutorial Db2 subnet group' \\\n"
                f"    --subnet-ids {ids} \\\n"
                f"    --region {region}\n"
                f"then re-run with:  export DB2_DB_SUBNET_GROUP={group_name}"
            )
        return (
            "Could not find two subnets in different AZs to suggest. Create subnets "
            "in at least two AZs, then create a DB subnet group spanning them."
        )
    except Exception as e:  # pragma: no cover - best effort
        return (
            "Create a DB subnet group spanning two AZs, then set "
            f"DB2_DB_SUBNET_GROUP. (Could not auto-list subnets: {e})"
        )


def banner(title: str):
    """Pretty section header so lab output is easy to read."""
    line = "=" * 70
    print(f"\n{line}\n  {title}\n{line}")
