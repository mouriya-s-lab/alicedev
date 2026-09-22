"""Pure convergence判定 for the upgrade-bot E2E loop.

No daemon, filesystem, clock, or model output is involved here.  The
conductor supplies one typed :class:`RoundSummary` per E2E round and uses the
result to decide whether to continue or escalate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


NON_CONVERGING_LIMIT = 5


@dataclass(frozen=True)
class RoundSummary:
    """The failure surface produced by one before/after E2E round."""

    failing_checks: frozenset[str]
    error_signatures: frozenset[str]
    distance: int

    def __post_init__(self) -> None:
        if isinstance(self.distance, bool) or not isinstance(self.distance, int):
            raise TypeError("distance must be an integer")
        if self.distance < 0:
            raise ValueError("distance must be non-negative")

    @classmethod
    def from_values(
        cls,
        failing_checks: Iterable[str],
        error_signatures: Iterable[str],
        distance: int,
    ) -> "RoundSummary":
        """Construct an immutable summary from adapter-owned collections."""

        return cls(frozenset(failing_checks), frozenset(error_signatures), distance)

    @property
    def failures(self) -> frozenset[str]:
        """Short name used by the §15.3 rule (``F``)."""

        return self.failing_checks

    @property
    def errors(self) -> frozenset[str]:
        """Short name used by the §15.3 rule (``E``)."""

        return self.error_signatures

    @property
    def is_green(self) -> bool:
        """Whether this round is exactly green (``F = E = ∅`` and ``d = 0``)."""

        return not self.failing_checks and not self.error_signatures and self.distance == 0

    def as_dict(self) -> dict[str, object]:
        """Return the stable callback/evidence representation."""

        return {
            "failing_checks": sorted(self.failing_checks),
            "error_signatures": sorted(self.error_signatures),
            "distance": self.distance,
        }

    # The prior conductor called this ``to_json``; retaining a pure serializer
    # makes migration of evidence consumers boring without adding a second
    # domain type.
    def to_json(self) -> dict[str, object]:
        return self.as_dict()


class ConvergenceDecision(str, Enum):
    """The result of comparing the current round with its predecessor."""

    GREEN = "green"
    CONVERGING = "converging"
    NON_CONVERGING = "non_converging"


@dataclass(frozen=True)
class ConvergenceResult:
    """A pure decision plus the streak contribution for the current round."""

    decision: ConvergenceDecision
    non_converging_streak: int

    @property
    def is_green(self) -> bool:
        return self.decision is ConvergenceDecision.GREEN

    @property
    def is_converging(self) -> bool:
        return self.decision is ConvergenceDecision.CONVERGING

    @property
    def is_non_converging(self) -> bool:
        return self.decision is ConvergenceDecision.NON_CONVERGING


# ``F`` and ``E`` must each shrink monotonically, ``d`` may not increase, and
# at least one dimension must strictly decrease.  Equality in every dimension
# is a plateau and therefore deliberately returns False.
def is_converging(previous: RoundSummary, current: RoundSummary) -> bool:
    """Evaluate the §15.3 pairwise convergence rule."""

    monotone = (
        current.failing_checks <= previous.failing_checks
        and current.error_signatures <= previous.error_signatures
        and current.distance <= previous.distance
    )
    if not monotone:
        return False
    return (
        current.failing_checks < previous.failing_checks
        or current.error_signatures < previous.error_signatures
        or current.distance < previous.distance
    )


def assess(previous: RoundSummary | None, current: RoundSummary) -> ConvergenceResult:
    """Classify one round without retaining mutable tracker state.

    A first non-green round has no predecessor to prove progress against, so it
    is conservatively counted as non-converging.  This makes the literal
    ``five consecutive non-converging rounds`` rule terminate after five
    observed plateau/regression rounds rather than silently requiring six.
    """

    if current.is_green:
        return ConvergenceResult(ConvergenceDecision.GREEN, 0)
    if previous is not None and is_converging(previous, current):
        return ConvergenceResult(ConvergenceDecision.CONVERGING, 0)
    return ConvergenceResult(ConvergenceDecision.NON_CONVERGING, 1)


def evaluate(previous: RoundSummary | None, current: RoundSummary) -> ConvergenceDecision:
    """Small convenience wrapper for callers that only need the decision."""

    return assess(previous, current).decision


# Compatibility vocabulary for callers that use the contract's F/E naming.
RoundResult = RoundSummary
Decision = ConvergenceDecision

__all__ = [
    "ConvergenceDecision",
    "ConvergenceResult",
    "Decision",
    "NON_CONVERGING_LIMIT",
    "RoundResult",
    "RoundSummary",
    "assess",
    "evaluate",
    "is_converging",
]
