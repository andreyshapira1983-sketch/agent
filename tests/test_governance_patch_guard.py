"""Тесты защиты от looping patch: cooldown и лимит патчей на файл."""
import pytest

# В тестах отключаем лимит по количеству для первого файла и проверяем cooldown/budget
from src.governance.patch_guard import (
    can_patch,
    record_patch,
    reset_all,
    reset_file,
    advance_cycle,
    PATCH_COOLDOWN_CYCLES,
    MAX_PATCHES_PER_FILE,
)


def test_can_patch_first_time():
    reset_all()
    allowed, _ = can_patch("src/foo/bar.py")
    assert allowed is True


def test_record_and_budget():
    reset_all()
    if MAX_PATCHES_PER_FILE <= 0:
        pytest.skip("MAX_PATCHES_PER_FILE=0, budget disabled")
    path = "tests/_patch_guard_dummy.py"
    for i in range(MAX_PATCHES_PER_FILE):
        if i > 0:
            for _ in range(PATCH_COOLDOWN_CYCLES):
                advance_cycle()
        allowed, _ = can_patch(path)
        assert allowed is True, f"iteration {i}"
        record_patch(path)
    allowed, reason = can_patch(path)
    assert allowed is False
    assert "locked" in reason or str(MAX_PATCHES_PER_FILE) in reason
    reset_file(path)
    allowed, _ = can_patch(path)
    assert allowed is True


def test_cooldown_blocks_immediate_repatch():
    reset_all()
    if PATCH_COOLDOWN_CYCLES <= 0:
        pytest.skip("PATCH_COOLDOWN_CYCLES=0, cooldown disabled")
    path = "tests/_patch_guard_cooldown.py"
    allowed, _ = can_patch(path)
    assert allowed is True
    record_patch(path)
    allowed, reason = can_patch(path)
    assert allowed is False
    assert "Cooldown" in reason or "cooldown" in reason
    reset_file(path)


def test_advance_cycle_and_reset_all():
    reset_all()
    advance_cycle()
    advance_cycle()
    reset_all()
