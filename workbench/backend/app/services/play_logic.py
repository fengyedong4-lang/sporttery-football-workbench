from __future__ import annotations

from collections.abc import Mapping


RESULTS = ("胜", "平", "负")
HANDICAP_RESULTS = ("让胜", "让平", "让负")


def result_for_difference(difference: int) -> str:
    return "胜" if difference > 0 else "平" if difference == 0 else "负"


def handicap_result_for_difference(difference: int, handicap: int) -> str:
    adjusted = difference + handicap
    return "让胜" if adjusted > 0 else "让平" if adjusted == 0 else "让负"


def compatible(result: str, handicap_result: str, handicap: int, limit: int = 20) -> bool:
    return any(
        result_for_difference(d) == result
        and handicap_result_for_difference(d, handicap) == handicap_result
        for d in range(-limit, limit + 1)
    )


def aggregate_difference_probabilities(
    differences: Mapping[int, float], handicap: int | None
) -> tuple[dict[str, float], dict[str, float] | None]:
    result = {key: 0.0 for key in RESULTS}
    handicap_result = {key: 0.0 for key in HANDICAP_RESULTS} if handicap is not None else None
    for difference, probability in differences.items():
        result[result_for_difference(difference)] += probability
        if handicap_result is not None:
            handicap_result[handicap_result_for_difference(difference, handicap)] += probability
    return result, handicap_result


def unique_pick(probabilities: Mapping[str, float]) -> str:
    if not probabilities:
        raise ValueError("概率为空")
    return max(probabilities, key=lambda key: probabilities[key])

