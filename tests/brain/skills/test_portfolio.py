"""Tests for brain/skills/portfolio.py — Portfolio."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from brain.skills.portfolio import (
    Portfolio,
    PortfolioEntry,
    _confidence,
    _smoothed_rating,
)


def _make_entry(profession_id: str = "text_editor", success: bool = True, **kw) -> PortfolioEntry:
    base = dict(
        id="auto",
        profession_id=profession_id,
        job_id=kw.pop("job_id", "j-" + profession_id),
        success=success,
        delivered_at=datetime.now(timezone.utc).isoformat(),
        tokens_used=kw.pop("tokens_used", 1000),
        dollars_spent=kw.pop("dollars_spent", 0.01),
        duration_sec=kw.pop("duration_sec", 12.3),
    )
    base.update(kw)
    return PortfolioEntry(**base)


# ════════════════════════════════════════════════════════════════════
# Recording
# ════════════════════════════════════════════════════════════════════

class TestRecording:

    def test_record_assigns_id(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        e = port.record(_make_entry())
        assert e.id != "auto"
        assert len(e.id) > 8
        assert len(port) == 1
        port.close()

    def test_record_persists(self, tmp_path):
        path = tmp_path / "p.db"
        port = Portfolio(path)
        port.record(_make_entry())
        port.close()

        port2 = Portfolio(path)
        assert len(port2) == 1
        port2.close()


# ════════════════════════════════════════════════════════════════════
# Stats
# ════════════════════════════════════════════════════════════════════

class TestStats:

    def test_unknown_profession_returns_zero_stats(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        s = port.stats("nobody")
        assert s.total == 0
        assert s.successes == 0
        # smoothed rating: 1/2 = 0.5 (neutral prior)
        assert s.rating == pytest.approx(0.5)
        assert s.confidence == 0.0
        port.close()

    def test_three_successes(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        for i in range(3):
            port.record(_make_entry(success=True, tokens_used=100, dollars_spent=0.02, job_id=f"j{i}"))
        s = port.stats("text_editor")
        assert s.total == 3
        assert s.successes == 3
        assert s.failures == 0
        # 4/5 = 0.8 with Laplace
        assert s.rating == pytest.approx(0.8)
        assert s.tokens_total == 300
        assert s.dollars_total == pytest.approx(0.06)
        port.close()

    def test_mixed_outcomes(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        for i in range(4):
            port.record(_make_entry(success=True, job_id=f"s{i}"))
        for i in range(2):
            port.record(_make_entry(success=False, job_id=f"f{i}"))
        s = port.stats("text_editor")
        assert s.successes == 4 and s.failures == 2
        # (4+1)/(6+2) = 5/8 = 0.625
        assert s.rating == pytest.approx(0.625)
        assert s.confidence > 0
        port.close()


# ════════════════════════════════════════════════════════════════════
# Ranking
# ════════════════════════════════════════════════════════════════════

class TestRanking:

    def test_rank_prefers_proven_profession(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        # text_editor: 5 wins
        for i in range(5):
            port.record(_make_entry("text_editor", success=True, job_id=f"e{i}"))
        # translator: 1 win 1 loss
        port.record(_make_entry("translator_en_ru", success=True,  job_id="t1"))
        port.record(_make_entry("translator_en_ru", success=False, job_id="t2"))

        ranked = port.rank_professions(["translator_en_ru", "text_editor"])
        assert ranked[0].profession_id == "text_editor"
        assert ranked[1].profession_id == "translator_en_ru"
        port.close()

    def test_untested_falls_to_bottom(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        port.record(_make_entry("text_editor", success=True, job_id="e1"))
        ranked = port.rank_professions(["new_profession", "text_editor"])
        assert ranked[0].profession_id == "text_editor"
        assert ranked[1].profession_id == "new_profession"
        port.close()


# ════════════════════════════════════════════════════════════════════
# History
# ════════════════════════════════════════════════════════════════════

class TestHistory:

    def test_history_returns_most_recent_first(self, tmp_path):
        port = Portfolio(tmp_path / "p.db")
        import time
        for i in range(3):
            port.record(_make_entry(job_id=f"j{i}"))
            time.sleep(0.01)
        history = port.history("text_editor", limit=10)
        assert len(history) == 3
        # Most recent (j2) first
        assert history[0].job_id == "j2"
        port.close()


# ════════════════════════════════════════════════════════════════════
# Math helpers
# ════════════════════════════════════════════════════════════════════

class TestMath:

    def test_smoothed_rating(self):
        assert _smoothed_rating(0, 0) == pytest.approx(0.5)
        assert _smoothed_rating(10, 10) == pytest.approx(11/12)
        assert _smoothed_rating(0, 10) == pytest.approx(1/12)

    def test_confidence(self):
        assert _confidence(0) == 0.0
        assert _confidence(1) == pytest.approx(1 - 1/(2 ** 0.5))
        assert 0 < _confidence(50) < 1
        assert _confidence(50) > _confidence(10)
