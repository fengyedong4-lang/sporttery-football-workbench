from app.services.play_logic import compatible, handicap_result_for_difference, result_for_difference


def test_h_minus_two_boundaries():
    assert handicap_result_for_difference(1, -2) == "让负"
    assert handicap_result_for_difference(2, -2) == "让平"
    assert handicap_result_for_difference(3, -2) == "让胜"


def test_positive_zero_negative_handicaps():
    assert handicap_result_for_difference(-1, 1) == "让平"
    assert handicap_result_for_difference(0, 0) == "让平"
    assert handicap_result_for_difference(1, -1) == "让平"
    assert result_for_difference(-1) == "负"


def test_incompatible_draw_and_handicap_win_at_minus_one():
    assert not compatible("平", "让胜", -1)
    assert compatible("胜", "让平", -1)

