from __future__ import annotations

from src.learning import feedback
from src.communication.telegram_commands import add_rating_from_text


def test_rate_last_feedback_prefers_last_unrated(monkeypatch) -> None:
    monkeypatch.setattr(feedback, "_save", lambda: None)
    monkeypatch.setattr(
        feedback,
        "_log",
        [
            {"request": "a", "response": "ra", "rating": 0.2},
            {"request": "b", "response": "rb", "rating": None},
            {"request": "c", "response": "rc", "rating": None},
        ],
    )

    ok = feedback.rate_last_feedback(0.8)

    assert ok is True
    assert feedback._log[-1]["rating"] == 0.8
    assert feedback._log[1]["rating"] is None


def test_add_rating_from_text_accepts_five_point_scale(monkeypatch) -> None:
    monkeypatch.setattr(feedback, "_save", lambda: None)
    monkeypatch.setattr(
        feedback,
        "_log",
        [{"request": "q", "response": "a", "rating": None}],
    )

    text = add_rating_from_text("5")

    assert "Принял оценку" in text
    assert feedback._log[-1]["rating"] == 1.0


def test_add_rating_from_text_rejects_out_of_range() -> None:
    text = add_rating_from_text("8")
    assert "вне диапазона" in text
