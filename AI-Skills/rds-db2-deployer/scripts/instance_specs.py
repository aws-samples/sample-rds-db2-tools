"""Grounded RDS-for-Db2 instance-class specifications (vCPU + memory).

This module is the single grounded source the edition reconciliation (task 3.2,
R8.7) reads vCPU and memory from, rather than a hardcoded guess at the call
site. The Standard Edition licensing ceiling (<=32 vCPU AND <=128 GB memory,
R8.4) is meaningless without a trustworthy vCPU/memory lookup, so the design
requires those numbers come from "the RDS-published instance-class
specifications" (R8.7, consistent with the truth-grounding requirement R5).

Design intent (so this can be backed by live RDS data later, not rewritten):

* The lookup is expressed as a plain data table (``INSTANCE_SPECS``) keyed by
  the exact RDS instance-class string (``db.<family>.<size>``). Every value in
  the table is a *published* AWS spec, not an invented number. The seed entries
  are exactly the classes the design's Workload_Sizing_Map and edition note name
  (R8 design note): ``db.t3.small``, ``db.t3.xlarge``, ``db.r7i.2xlarge``,
  ``db.r7i.4xlarge``, ``db.x2iedn.16xlarge`` -- plus the prod-posture default
  ``db.r7i.xlarge``.
* ``lookup_instance_spec`` is the only entry point. It first consults the static
  table, then falls back to deriving the spec arithmetically from the family's
  published per-vCPU memory ratio and the canonical EC2/RDS size ladder. The
  fallback is itself grounded: each family's ratio (e.g. ``r`` = 8 GiB/vCPU,
  ``x2iedn`` = 16 GiB/vCPU, ``t3`` = 4 GiB/vCPU) is the AWS-published ratio, and
  the size ladder (``large`` = 2 vCPU, doubling per step) is the canonical AWS
  vCPU progression. This lets a future ``r8i``/``r8a``/``x8i`` class resolve
  correctly without a table edit, while still refusing to fabricate a number for
  a family whose ratio we do not know.
* A real implementation MAY replace the table/fallback with a live
  ``aws ec2 describe-instance-types`` / RDS offering query; the function
  signature and the :class:`InstanceSpec` shape are the stable contract the
  resolver depends on, so swapping the backing source is a drop-in change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class InstanceSpec:
    """The grounded compute spec of an RDS instance class.

    ``vcpu`` is the vCPU count and ``memory_gib`` is the memory in GiB, both as
    published by AWS for the EC2 instance type underlying the RDS class.
    ``source`` records where the value came from (``published-table`` for a
    seeded entry, ``derived-from-family-ratio`` for the arithmetic fallback) so
    callers and audits can tell a looked-up fact from a derived one.
    """

    instance_class: str
    vcpu: int
    memory_gib: float
    source: str


class UnknownInstanceClassError(Exception):
    """The instance class is not in the grounded table and its family ratio is
    unknown, so its vCPU/memory cannot be determined without fabrication.

    Per the truth-grounding requirement (R8.7, R5) the resolver MUST NOT guess;
    it surfaces this so the caller can ask for a grounded value instead.
    """

    def __init__(self, instance_class: str) -> None:
        self.instance_class = instance_class
        super().__init__(
            f"No grounded vCPU/memory spec for instance class {instance_class!r}. "
            "Add it to the published instance-spec table (or supply a family "
            "whose published memory-per-vCPU ratio is known) rather than "
            "guessing its size."
        )


# ---------------------------------------------------------------------------
# Grounded published specifications
# ---------------------------------------------------------------------------

# Static table of AWS-published vCPU/memory specs for the instance classes the
# design names directly. Each row is a real published spec (EC2 instance type
# underlying the RDS class). Sources cross-checked against the design's edition
# note: r7i.2xlarge = 8 vCPU / 64 GiB; r7i.4xlarge = 16 vCPU / 128 GiB (memory
# exactly at the inclusive SE ceiling); x2iedn.16xlarge = 64 vCPU / 1024 GiB.
INSTANCE_SPECS: dict[str, InstanceSpec] = {
    # General-purpose burstable (t3): 4 GiB/vCPU.
    "db.t3.small": InstanceSpec("db.t3.small", 2, 2.0, "published-table"),
    "db.t3.xlarge": InstanceSpec("db.t3.xlarge", 4, 16.0, "published-table"),
    # Memory-optimized r7i: 8 GiB/vCPU.
    "db.r7i.xlarge": InstanceSpec("db.r7i.xlarge", 4, 32.0, "published-table"),
    "db.r7i.2xlarge": InstanceSpec("db.r7i.2xlarge", 8, 64.0, "published-table"),
    "db.r7i.4xlarge": InstanceSpec("db.r7i.4xlarge", 16, 128.0, "published-table"),
    # High-memory x2iedn: 16 GiB/vCPU.
    "db.x2iedn.16xlarge": InstanceSpec(
        "db.x2iedn.16xlarge", 64, 1024.0, "published-table"
    ),
}


# Published memory-per-vCPU ratios (GiB per vCPU) for the families the skill
# supports, used by the arithmetic fallback for sizes not in the static table.
# These are the AWS-published family ratios; they let a future x86 class in a
# known family (e.g. r8i succeeding r7i) resolve without a table edit (R17.16),
# while an unknown family still raises rather than fabricating a number.
_FAMILY_MEMORY_PER_VCPU_GIB: dict[str, float] = {
    # burstable / general purpose
    "t3": 4.0,
    "t4g": 4.0,
    "m5": 4.0,
    "m6i": 4.0,
    "m7i": 4.0,
    # memory optimized (8 GiB/vCPU)
    "r5": 8.0,
    "r6i": 8.0,
    "r6a": 8.0,
    "r7i": 8.0,
    "r7a": 8.0,
    "r8i": 8.0,
    "r8a": 8.0,
    # high-memory optimized
    "x2iedn": 16.0,
    "x2idn": 16.0,
    "x8i": 16.0,
}


# Canonical AWS vCPU ladder by size token. ``large`` = 2 vCPU and each step
# doubles, matching the published EC2 progression. ``16xlarge`` = 64 vCPU lines
# up with the x2iedn.16xlarge table entry as a cross-check.
_SIZE_VCPU: dict[str, int] = {
    "medium": 1,
    "large": 2,
    "xlarge": 4,
    "2xlarge": 8,
    "4xlarge": 16,
    "8xlarge": 32,
    "12xlarge": 48,
    "16xlarge": 64,
    "24xlarge": 96,
    "32xlarge": 128,
    "48xlarge": 192,
}


def _family_of(instance_class: str) -> Optional[str]:
    """Return the family token (``db.r7i.2xlarge`` -> ``r7i``) or ``None``."""
    parts = instance_class.split(".")
    if len(parts) < 3:
        return None
    return parts[1] or None


def _size_of(instance_class: str) -> Optional[str]:
    """Return the size token (``db.r7i.2xlarge`` -> ``2xlarge``) or ``None``."""
    parts = instance_class.split(".")
    if len(parts) < 3:
        return None
    return parts[2] or None


def lookup_instance_spec(instance_class: str) -> InstanceSpec:
    """Return the grounded :class:`InstanceSpec` for ``instance_class``.

    Resolution order:

    1. The published static table (exact published vCPU/memory).
    2. Arithmetic derivation from the family's published memory-per-vCPU ratio
       and the canonical vCPU size ladder, for a known family/size not yet
       tabulated.

    Raises:
        UnknownInstanceClassError: neither path can ground the spec, so the
            resolver must not guess (R8.7).
    """
    if instance_class in INSTANCE_SPECS:
        return INSTANCE_SPECS[instance_class]

    family = _family_of(instance_class)
    size = _size_of(instance_class)
    if family is not None and size is not None:
        ratio = _FAMILY_MEMORY_PER_VCPU_GIB.get(family)
        vcpu = _SIZE_VCPU.get(size)
        if ratio is not None and vcpu is not None:
            return InstanceSpec(
                instance_class,
                vcpu,
                vcpu * ratio,
                "derived-from-family-ratio",
            )

    raise UnknownInstanceClassError(instance_class)
