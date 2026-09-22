from __future__ import annotations

import sys
from pathlib import Path

# The plugin package is mounted as ``bot/`` in production rather than installed
# into the repository interpreter.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alicedev.upgrade.convergence import (  # noqa: E402
    ConvergenceDecision,
    NON_CONVERGING_LIMIT,
    RoundSummary,
    assess,
    is_converging,
)


def _round(failures: set[str], errors: set[str], distance: int) -> RoundSummary:
    return RoundSummary.from_values(failures, errors, distance)


def test_table_of_pairwise_convergence_decisions() -> None:
    cases = (
        (
            "monotone shrink",
            _round({"a", "b"}, {"sig"}, 3),
            _round({"a"}, {"sig"}, 2),
            True,
        ),
        (
            "distance increases",
            _round({"a"}, {"sig"}, 1),
            _round(set(), set(), 2),
            False,
        ),
        (
            "oscillation introduces a prior failure",
            _round({"a"}, {"sig"}, 1),
            _round({"b"}, {"sig"}, 1),
            False,
        ),
        (
            "plateau is not strict progress",
            _round({"a"}, {"sig"}, 1),
            _round({"a"}, {"sig"}, 1),
            False,
        ),
    )
    for _name, previous, current, expected in cases:
        assert is_converging(previous, current) is expected


def test_green_is_distinct_from_converging() -> None:
    green = _round(set(), set(), 0)
    assert green.is_green
    result = assess(_round({"a"}, {"sig"}, 2), green)
    assert result.decision is ConvergenceDecision.GREEN
    assert result.non_converging_streak == 0


def test_five_consecutive_non_converging_rounds_escalate() -> None:
    plateau = _round({"same"}, {"same-error"}, 1)
    previous: RoundSummary | None = None
    streak = 0

    for round_index in range(1, NON_CONVERGING_LIMIT + 1):
        result = assess(previous, plateau)
        if result.decision is ConvergenceDecision.NON_CONVERGING:
            streak += 1
        else:
            streak = 0
        previous = plateau
        assert streak == round_index

    assert streak == NON_CONVERGING_LIMIT
