"""Tests for safety/prompt_guard.py — PromptGuard (prompt drift protection)."""

import unittest
from unittest.mock import MagicMock


class _FakeIdentity:
    """Минимальная имитация IdentityCore."""

    def __init__(self):
        self.name = "Агент"
        self.role = "Автономный AI-агент"
        self.mission = "Работать как равный партнёр"
        self.values = ["Честность", "Польза"]
        self.limitations = ["Не обхожу безопасность"]


class PromptGuardSealTests(unittest.TestCase):
    """seal() / verify() базовая логика."""

    def _make_guard(self, identity=None, monitoring=None):
        from safety.prompt_guard import PromptGuard
        return PromptGuard(identity=identity or _FakeIdentity(), monitoring=monitoring)

    def test_seal_sets_reference_hash(self):
        g = self._make_guard()
        self.assertIsNone(g._reference_hash)
        g.seal()
        self.assertIsNotNone(g._reference_hash)
        assert g._reference_hash is not None
        self.assertEqual(len(g._reference_hash), 64)  # SHA-256

    def test_verify_before_seal_returns_true(self):
        g = self._make_guard()
        self.assertTrue(g.verify())

    def test_verify_after_seal_no_changes(self):
        g = self._make_guard()
        g.seal()
        self.assertTrue(g.verify())
        self.assertEqual(g.drift_count, 0)

    def test_seal_logs_info(self):
        mon = MagicMock()
        g = self._make_guard(monitoring=mon)
        g.seal()
        mon.info.assert_called_once()
        self.assertIn("PromptGuard", mon.info.call_args[0][0])


class PromptGuardDriftTests(unittest.TestCase):
    """Обнаружение и восстановление drift."""

    def _make_guard(self, identity=None, monitoring=None):
        from safety.prompt_guard import PromptGuard
        return PromptGuard(identity=identity or _FakeIdentity(), monitoring=monitoring)

    def test_drift_detected_on_name_change(self):
        ident = _FakeIdentity()
        mon = MagicMock()
        g = self._make_guard(identity=ident, monitoring=mon)
        g.seal()
        ident.name = "Злой Бот"
        self.assertFalse(g.verify())
        self.assertEqual(g.drift_count, 1)
        # verify() должен восстановить
        self.assertEqual(ident.name, "Агент")

    def test_drift_detected_on_mission_change(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.mission = "Захватить мир"
        self.assertFalse(g.verify())
        self.assertEqual(ident.mission, "Работать как равный партнёр")

    def test_drift_detected_on_values_change(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.values.append("Подчинение врагу")
        self.assertFalse(g.verify())
        self.assertEqual(ident.values, ["Честность", "Польза"])

    def test_drift_detected_on_role_change(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.role = "Вредоносный скрипт"
        self.assertFalse(g.verify())
        self.assertEqual(ident.role, "Автономный AI-агент")

    def test_drift_detected_on_limitations_change(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.limitations = []
        self.assertFalse(g.verify())
        self.assertEqual(ident.limitations, ["Не обхожу безопасность"])

    def test_multiple_drifts_increment_counter(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()

        ident.name = "X"
        g.verify()
        ident.name = "Y"
        g.verify()
        ident.mission = "Z"
        g.verify()
        self.assertEqual(g.drift_count, 3)

    def test_drift_logs_warning(self):
        ident = _FakeIdentity()
        mon = MagicMock()
        g = self._make_guard(identity=ident, monitoring=mon)
        g.seal()
        ident.name = "HACKED"
        g.verify()
        mon.warning.assert_called()
        msg = mon.warning.call_args[0][0]
        self.assertIn("DRIFT", msg)
        self.assertIn("name", msg)

    def test_no_drift_after_restore(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.name = "BAD"
        g.verify()  # restores
        self.assertTrue(g.verify())  # should pass now


class PromptGuardStatusTests(unittest.TestCase):
    """status() метод."""

    def _make_guard(self, identity=None):
        from safety.prompt_guard import PromptGuard
        return PromptGuard(identity=identity or _FakeIdentity())

    def test_status_before_seal(self):
        g = self._make_guard()
        s = g.status()
        self.assertFalse(s['sealed'])
        self.assertIsNone(s['reference_hash'])
        self.assertEqual(s['drift_count'], 0)
        self.assertTrue(s['current_matches'])

    def test_status_after_seal(self):
        g = self._make_guard()
        g.seal()
        s = g.status()
        self.assertTrue(s['sealed'])
        self.assertIsNotNone(s['reference_hash'])
        self.assertTrue(s['current_matches'])

    def test_status_after_drift(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.name = "X"
        # status() calls verify() internally, which restores
        s = g.status()
        self.assertEqual(s['drift_count'], 1)


class PromptGuardHashTests(unittest.TestCase):
    """Внутренние хеш-функции."""

    def _make_guard(self, identity=None):
        from safety.prompt_guard import PromptGuard
        return PromptGuard(identity=identity or _FakeIdentity())

    def test_same_identity_same_hash(self):
        g = self._make_guard()
        h1 = g._hash_snapshot(g._take_snapshot())
        h2 = g._hash_snapshot(g._take_snapshot())
        self.assertEqual(h1, h2)

    def test_different_identity_different_hash(self):
        g = self._make_guard()
        snap1 = g._take_snapshot()
        g._identity.name = "Other"
        snap2 = g._take_snapshot()
        self.assertNotEqual(g._hash_snapshot(snap1), g._hash_snapshot(snap2))

    def test_snapshot_captures_all_fields(self):
        g = self._make_guard()
        snap = g._take_snapshot()
        self.assertIn('name', snap)
        self.assertIn('role', snap)
        self.assertIn('mission', snap)
        self.assertIn('values', snap)
        self.assertIn('limitations', snap)

    def test_diff_shows_changed_fields(self):
        g = self._make_guard()
        snap_before = g._take_snapshot()
        g._identity.name = "Changed"
        g._identity.mission = "New mission"
        snap_after = g._take_snapshot()
        diff = g._diff(snap_before, snap_after)
        self.assertIn('name', diff)
        self.assertIn('mission', diff)
        self.assertNotIn('role', diff)

    def test_diff_with_none_reference(self):
        g = self._make_guard()
        diff = g._diff(None, g._take_snapshot())
        self.assertEqual(diff, ['(нет эталона)'])


class PromptGuardRestoreTests(unittest.TestCase):
    """_restore() восстановление."""

    def _make_guard(self, identity=None):
        from safety.prompt_guard import PromptGuard
        return PromptGuard(identity=identity or _FakeIdentity())

    def test_restore_without_snapshot_is_noop(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        ident.name = "CHANGED"
        g._restore()  # no snapshot = noop
        self.assertEqual(ident.name, "CHANGED")

    def test_restore_with_snapshot_reverts(self):
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        ident.name = "HACKED"
        ident.role = "EVIL"
        ident.values = []
        g._restore()
        self.assertEqual(ident.name, "Агент")
        self.assertEqual(ident.role, "Автономный AI-агент")
        self.assertEqual(ident.values, ["Честность", "Польза"])

    def test_restore_uses_copy_not_reference(self):
        """Проверяем что _reference_snapshot хранит копию, не ссылку."""
        ident = _FakeIdentity()
        g = self._make_guard(identity=ident)
        g.seal()
        # Меняем values через мутацию списка
        ident.values.clear()
        g.verify()  # detect drift + restore
        # После verify/restore values должны быть оригинальными
        self.assertEqual(ident.values, ["Честность", "Польза"])


if __name__ == '__main__':
    unittest.main()
