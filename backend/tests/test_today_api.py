from datetime import date

from app.api.today import _next_action


def test_next_action_requires_data_first() -> None:
    result = _next_action(None, [], [], None)
    assert result["key"] == "sync_data"


def test_next_action_prioritizes_watchlist_or_alert_risk() -> None:
    result = _next_action(
        {"phase": "rally", "state": "strong"},
        [{"priority": "risk"}],
        [],
        date(2026, 9, 6),
    )
    assert result["key"] == "review_risk"


def test_next_action_uses_market_phase_when_no_risk() -> None:
    result = _next_action(
        {"phase": "rally", "state": "range"},
        [],
        [],
        date(2026, 9, 6),
    )
    assert result["key"] == "review_mainline"


def test_next_action_de_risks_weak_market() -> None:
    result = _next_action(
        {"phase": "ebb", "state": "lean_weak"},
        [],
        [],
        date(2026, 9, 6),
    )
    assert result["key"] == "reduce_risk"
