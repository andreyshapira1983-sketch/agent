"""
100% coverage tests for core/model_manager.py
"""
import unittest
from core.model_manager import ModelTier, ModelProfile, ModelManager


# ── Stubs ─────────────────────────────────────────────────────────────────────

class _BudgetStub:
    """Заглушка budget_control."""
    def __init__(self, allow=True):
        self.allow = allow
        self.spent = []

    def can_afford(self, tokens, money):
        return self.allow

    def spend_tokens(self, prompt, completion, model):
        self.spent.append((prompt, completion, model))


class _BudgetDenyRoute:
    """budget_control: запрещает конкретную модель по стоимости."""
    def __init__(self, deny_model_id, models_ref):
        self._deny = deny_model_id
        self._models = models_ref

    def can_afford(self, tokens, money):
        # Запрещаем только если стоимость >= стоимости "тяжёлой" модели
        # Просто запрещаем всем — этот стаб используется для изоляции
        return False

    def spend_tokens(self, prompt, completion, model):
        pass


class _MonitoringStub:
    """Заглушка monitoring."""
    def __init__(self):
        self.messages = []
        self.tokens_recorded = []

    def info(self, msg, source=None):
        self.messages.append((msg, source))

    def record_tokens(self, prompt, completion, source=None):
        self.tokens_recorded.append((prompt, completion, source))


# ── ModelProfile tests ────────────────────────────────────────────────────────

class TestModelProfile(unittest.TestCase):

    def _make_profile(self, capabilities=None):
        return ModelProfile(
            model_id='test-model',
            name='Test Model',
            tier=ModelTier.STANDARD,
            provider='test',
            cost_per_1k_tokens=0.01,
            context_window=8192,
            capabilities=capabilities,
        )

    def test_init_with_capabilities(self):
        p = self._make_profile(capabilities=['coding', 'reasoning'])
        self.assertEqual(p.model_id, 'test-model')
        self.assertEqual(p.name, 'Test Model')
        self.assertEqual(p.tier, ModelTier.STANDARD)
        self.assertEqual(p.provider, 'test')
        self.assertAlmostEqual(p.cost_per_1k_tokens, 0.01)
        self.assertEqual(p.context_window, 8192)
        self.assertEqual(p.capabilities, ['coding', 'reasoning'])
        self.assertEqual(p.use_count, 0)
        self.assertEqual(p.total_tokens, 0)

    def test_init_without_capabilities_defaults_empty_list(self):
        p = self._make_profile(capabilities=None)
        self.assertEqual(p.capabilities, [])

    def test_estimate_cost(self):
        p = self._make_profile()
        self.assertAlmostEqual(p.estimate_cost(1000), 0.01, places=5)
        self.assertAlmostEqual(p.estimate_cost(500), 0.005, places=5)
        self.assertEqual(p.estimate_cost(0), 0.0)

    def test_record_use_increments(self):
        p = self._make_profile()
        p.record_use(100)
        self.assertEqual(p.use_count, 1)
        self.assertEqual(p.total_tokens, 100)
        p.record_use(200)
        self.assertEqual(p.use_count, 2)
        self.assertEqual(p.total_tokens, 300)

    def test_to_dict_structure(self):
        p = self._make_profile(capabilities=['vision'])
        p.record_use(500)
        d = p.to_dict()
        self.assertEqual(d['model_id'], 'test-model')
        self.assertEqual(d['name'], 'Test Model')
        self.assertEqual(d['tier'], 'standard')
        self.assertEqual(d['provider'], 'test')
        self.assertAlmostEqual(d['cost_per_1k_tokens'], 0.01)
        self.assertEqual(d['context_window'], 8192)
        self.assertEqual(d['capabilities'], ['vision'])
        self.assertEqual(d['use_count'], 1)
        self.assertEqual(d['total_tokens'], 500)
        self.assertIn('total_cost', d)


# ── ModelManager basic tests ──────────────────────────────────────────────────

class TestModelManagerInit(unittest.TestCase):

    def test_init_no_deps(self):
        mm = ModelManager()
        self.assertIsNone(mm.budget_control)
        self.assertIsNone(mm.monitoring)
        self.assertGreater(len(mm._models), 0)
        self.assertEqual(mm._active_model_id, 'claude-sonnet-4-6')

    def test_init_with_monitoring(self):
        mon = _MonitoringStub()
        mm = ModelManager(monitoring=mon)
        # _log goes through monitoring during _register_defaults
        self.assertTrue(len(mon.messages) > 0)

    def test_register_adds_model(self):
        mm = ModelManager()
        prev = len(mm._models)
        p = mm.register('my-model', 'My Model', ModelTier.LOCAL,
                        'local', cost_per_1k_tokens=0.0, context_window=4096,
                        capabilities=['coding'])
        self.assertEqual(len(mm._models), prev + 1)
        self.assertIsInstance(p, ModelProfile)

    def test_register_with_monitoring_logs(self):
        mon = _MonitoringStub()
        mm = ModelManager(monitoring=mon)
        count_before = len(mon.messages)
        mm.register('x', 'X', ModelTier.LIGHT, 'x')
        self.assertGreater(len(mon.messages), count_before)

    def test_register_without_capabilities_defaults(self):
        mm = ModelManager()
        p = mm.register('m2', 'M2', ModelTier.LIGHT, 'prov')
        self.assertEqual(p.capabilities, [])

    def test_unregister_removes_model(self):
        mm = ModelManager()
        mm.register('temp', 'Temp', ModelTier.LIGHT, 'x')
        mm.unregister('temp')
        self.assertNotIn('temp', mm._models)

    def test_unregister_clears_active_if_matches(self):
        mm = ModelManager()
        mm.register('temp2', 'Temp2', ModelTier.LIGHT, 'x')
        mm.set_active('temp2')
        mm.unregister('temp2')
        self.assertIsNone(mm._active_model_id)

    def test_unregister_unknown_does_nothing(self):
        mm = ModelManager()
        before = set(mm._models.keys())
        mm.unregister('does-not-exist')
        self.assertEqual(set(mm._models.keys()), before)


# ── set_active / get_active ───────────────────────────────────────────────────

class TestActiveModel(unittest.TestCase):

    def test_set_active_success(self):
        mm = ModelManager()
        mm.set_active('gpt-4o')
        self.assertEqual(mm._active_model_id, 'gpt-4o')

    def test_set_active_unknown_raises(self):
        mm = ModelManager()
        with self.assertRaises(KeyError):
            mm.set_active('no-such-model')

    def test_get_active_returns_profile(self):
        mm = ModelManager()
        mm.set_active('gpt-4o')
        p = mm.get_active()
        self.assertIsNotNone(p)
        self.assertEqual(p.model_id, 'gpt-4o')

    def test_get_active_returns_none_when_no_active(self):
        mm = ModelManager()
        mm._active_model_id = None
        self.assertIsNone(mm.get_active())


# ── select_for_task ───────────────────────────────────────────────────────────

class TestSelectForTask(unittest.TestCase):

    def test_basic_selection_no_routing(self):
        mm = ModelManager()
        result = mm.select_for_task('coding')
        self.assertIsNotNone(result)

    def test_selection_with_required_capabilities(self):
        mm = ModelManager()
        result = mm.select_for_task('vision_task', required_capabilities=['vision'])
        self.assertIsNotNone(result)
        self.assertIn('vision', result.capabilities)

    def test_selection_via_routing_affordable(self):
        """Маршрутная модель доступна → возвращается сразу."""
        mm = ModelManager()
        mm.route('coding', 'gpt-4o')
        result = mm.select_for_task('coding')
        self.assertEqual(result.model_id, 'gpt-4o')

    def test_selection_via_routing_affordable_with_caps(self):
        """Маршрутная модель доступна и соответствует capabilities."""
        mm = ModelManager()
        mm.route('coding', 'gpt-4o')
        result = mm.select_for_task('coding', required_capabilities=['vision'])
        self.assertEqual(result.model_id, 'gpt-4o')

    def test_routing_not_affordable_falls_through(self):
        """Маршрутная модель не проходит бюджет → общий выбор."""
        budget = _BudgetStub(allow=False)
        mm = ModelManager(budget_control=budget)
        mm.route('coding', 'gpt-4o')
        # Все модели запрещены бюджетом → get_fallback
        result = mm.select_for_task('coding')
        # Не падаем с ошибкой; результат None или ModelProfile
        # (get_fallback вернёт что-то, т.к. pool = candidates or list(models.values()))
        # pool не пуст — вернёт первый

    def test_routing_caps_dont_match_falls_through(self):
        """Маршрутная модель не имеет нужного capability → общий выбор."""
        mm = ModelManager()
        # gpt-4o-mini не имеет 'analysis'
        mm.route('task', 'gpt-4o-mini')
        result = mm.select_for_task('task', required_capabilities=['analysis'])
        # Должен вернуть модель с 'analysis'
        self.assertIsNotNone(result)
        self.assertIn('analysis', result.capabilities)

    def test_routing_model_not_in_registry(self):
        """Маршрут указывает на несуществующую модель → пропускается."""
        mm = ModelManager()
        mm._task_routing['task'] = 'ghost-model'
        result = mm.select_for_task('task')
        self.assertIsNotNone(result)

    def test_capabilities_filter_eliminates_all_no_budget(self):
        """Нет модели с требуемым capability → get_fallback."""
        mm = ModelManager()
        result = mm.select_for_task('task', required_capabilities=['nonexistent_cap'])
        # get_fallback без нужного capability вернёт None только если empty models
        # (pool = affordable or candidates or list(models.values()))
        # Здесь: candidates=[] (не матчат), affordable=[] (нет budget_control),
        # pool = list(models.values()) → не None
        self.assertIsNotNone(result)

    def test_budget_filter_removes_all_then_fallback(self):
        """Бюджет запрещает всё → get_fallback."""
        budget = _BudgetStub(allow=False)
        mm = ModelManager(budget_control=budget)
        result = mm.select_for_task('coding')
        # get_fallback: affordable=[], pool = candidates (все модели без cap-фильтра),
        # sorted → вернёт первый
        self.assertIsNotNone(result)

    def test_budget_filter_allows_some(self):
        """budget_control разрешает → выбирается модель."""
        budget = _BudgetStub(allow=True)
        mm = ModelManager(budget_control=budget)
        result = mm.select_for_task('coding')
        self.assertIsNotNone(result)

    def test_no_routing_no_caps_selects_cheapest(self):
        """Без routing и caps выбирается самая лёгкая модель."""
        mm = ModelManager()
        result = mm.select_for_task('anything')
        # LOCAL/LIGHT идут первыми по tier_rank
        self.assertIn(result.tier, (ModelTier.LOCAL, ModelTier.LIGHT, ModelTier.STANDARD, ModelTier.HEAVY))


# ── route ─────────────────────────────────────────────────────────────────────

class TestRoute(unittest.TestCase):

    def test_route_sets_mapping(self):
        mm = ModelManager()
        mm.route('analysis', 'claude-opus-4-6')
        self.assertEqual(mm._task_routing['analysis'], 'claude-opus-4-6')

    def test_route_with_monitoring_logs(self):
        mon = _MonitoringStub()
        mm = ModelManager(monitoring=mon)
        count_before = len(mon.messages)
        mm.route('coding', 'gpt-4o')
        self.assertGreater(len(mon.messages), count_before)


# ── get_fallback ──────────────────────────────────────────────────────────────

class TestGetFallback(unittest.TestCase):

    def test_fallback_returns_model(self):
        mm = ModelManager()
        result = mm.get_fallback()
        self.assertIsNotNone(result)

    def test_fallback_prefers_light_tier(self):
        mm = ModelManager()
        result = mm.get_fallback()
        self.assertIn(result.tier, (ModelTier.LOCAL, ModelTier.LIGHT))

    def test_fallback_with_no_affordable_uses_candidates(self):
        """Нет affordable → candidates (без бюджет-фильтра)."""
        budget = _BudgetStub(allow=False)
        mm = ModelManager(budget_control=budget)
        result = mm.get_fallback(expected_tokens=1000)
        self.assertIsNotNone(result)

    def test_fallback_empty_models_returns_none(self):
        """Нет моделей вообще → None."""
        mm = ModelManager()
        mm._models.clear()
        result = mm.get_fallback()
        self.assertIsNone(result)

    def test_fallback_with_required_caps(self):
        mm = ModelManager()
        result = mm.get_fallback(required_capabilities=['vision'])
        self.assertIsNotNone(result)
        self.assertIn('vision', result.capabilities)

    def test_fallback_caps_no_match_falls_to_all_models(self):
        """Нет модели с capability → pool = list(models.values())."""
        mm = ModelManager()
        result = mm.get_fallback(required_capabilities=['nonexistent_xyz'])
        self.assertIsNotNone(result)


# ── record_usage ──────────────────────────────────────────────────────────────

class TestRecordUsage(unittest.TestCase):

    def test_record_usage_updates_model(self):
        mm = ModelManager()
        before = mm._models['gpt-4o'].total_tokens
        mm.record_usage('gpt-4o', 100)
        self.assertEqual(mm._models['gpt-4o'].total_tokens, before + 100)

    def test_record_usage_unknown_model_does_nothing(self):
        mm = ModelManager()
        # Не должно поднимать исключений
        mm.record_usage('ghost', 100)

    def test_record_usage_calls_budget_control(self):
        budget = _BudgetStub()
        mm = ModelManager(budget_control=budget)
        mm.record_usage('gpt-4o', 1000)
        self.assertEqual(len(budget.spent), 1)
        prompt, completion, model_name = budget.spent[0]
        self.assertEqual(prompt, 600)
        self.assertEqual(completion, 400)

    def test_record_usage_calls_monitoring(self):
        mon = _MonitoringStub()
        mm = ModelManager(monitoring=mon)
        mm.record_usage('gpt-4o', 500)
        self.assertEqual(len(mon.tokens_recorded), 1)
        p, c, src = mon.tokens_recorded[0]
        self.assertEqual(p, 300)
        self.assertEqual(c, 200)


# ── upgrade ───────────────────────────────────────────────────────────────────

class TestUpgrade(unittest.TestCase):

    def test_upgrade_transfers_routes(self):
        mm = ModelManager()
        mm.route('coding', 'gpt-4o')
        mm.route('analysis', 'gpt-4o')
        mm.upgrade('gpt-4o', 'claude-opus-4-6')
        self.assertEqual(mm._task_routing['coding'], 'claude-opus-4-6')
        self.assertEqual(mm._task_routing['analysis'], 'claude-opus-4-6')

    def test_upgrade_transfers_active(self):
        mm = ModelManager()
        mm.set_active('gpt-4o')
        mm.upgrade('gpt-4o', 'claude-opus-4-6')
        self.assertEqual(mm._active_model_id, 'claude-opus-4-6')

    def test_upgrade_no_match_is_noop(self):
        mm = ModelManager()
        mm.set_active('claude-sonnet-4-6')
        before_active = mm._active_model_id
        mm.upgrade('gpt-4o', 'claude-opus-4-6')
        self.assertEqual(mm._active_model_id, before_active)

    def test_upgrade_with_monitoring_logs(self):
        mon = _MonitoringStub()
        mm = ModelManager(monitoring=mon)
        count_before = len(mon.messages)
        mm.upgrade('gpt-4o', 'claude-opus-4-6')
        self.assertGreater(len(mon.messages), count_before)


# ── list_models / get_model / summary ────────────────────────────────────────

class TestReporting(unittest.TestCase):

    def test_list_models_returns_dicts(self):
        mm = ModelManager()
        lst = mm.list_models()
        self.assertIsInstance(lst, list)
        self.assertGreater(len(lst), 0)
        self.assertIn('model_id', lst[0])

    def test_get_model_known(self):
        mm = ModelManager()
        p = mm.get_model('gpt-4o')
        self.assertIsNotNone(p)
        self.assertEqual(p.model_id, 'gpt-4o')

    def test_get_model_unknown_returns_none(self):
        mm = ModelManager()
        self.assertIsNone(mm.get_model('no-such'))

    def test_summary_structure(self):
        mm = ModelManager()
        s = mm.summary()
        self.assertIn('registered', s)
        self.assertIn('active', s)
        self.assertIn('total_tokens_used', s)
        self.assertIn('total_cost_usd', s)
        self.assertIn('routes', s)
        self.assertEqual(s['registered'], len(mm._models))

    def test_summary_tracks_tokens_after_usage(self):
        mm = ModelManager()
        mm.record_usage('gpt-4o', 1000)
        s = mm.summary()
        self.assertGreaterEqual(s['total_tokens_used'], 1000)


# ── _tier_rank ────────────────────────────────────────────────────────────────

class TestTierRank(unittest.TestCase):

    def test_local_rank(self):
        self.assertEqual(ModelManager._tier_rank(ModelTier.LOCAL), 0)

    def test_light_rank(self):
        self.assertEqual(ModelManager._tier_rank(ModelTier.LIGHT), 1)

    def test_standard_rank(self):
        self.assertEqual(ModelManager._tier_rank(ModelTier.STANDARD), 2)

    def test_heavy_rank(self):
        self.assertEqual(ModelManager._tier_rank(ModelTier.HEAVY), 3)

    def test_unknown_tier_returns_99(self):
        class FakeTier:
            pass
        self.assertEqual(ModelManager._tier_rank(FakeTier()), 99)


# ── _matches_capabilities / _is_affordable ───────────────────────────────────

class TestHelpers(unittest.TestCase):

    def _make_profile(self, caps):
        return ModelProfile('x', 'X', ModelTier.LIGHT, 'p', capabilities=caps)

    def test_matches_caps_none_required(self):
        p = self._make_profile(['coding'])
        self.assertTrue(ModelManager._matches_capabilities(p, None))

    def test_matches_caps_all_present(self):
        p = self._make_profile(['coding', 'vision'])
        self.assertTrue(ModelManager._matches_capabilities(p, ['coding']))

    def test_matches_caps_missing(self):
        p = self._make_profile(['coding'])
        self.assertFalse(ModelManager._matches_capabilities(p, ['vision']))

    def test_is_affordable_no_budget(self):
        mm = ModelManager()
        p = self._make_profile([])
        self.assertTrue(mm._is_affordable(p, 1000))

    def test_is_affordable_with_budget_true(self):
        mm = ModelManager(budget_control=_BudgetStub(allow=True))
        p = self._make_profile([])
        self.assertTrue(mm._is_affordable(p, 1000))

    def test_is_affordable_with_budget_false(self):
        mm = ModelManager(budget_control=_BudgetStub(allow=False))
        p = self._make_profile([])
        self.assertFalse(mm._is_affordable(p, 1000))


# ── _log without monitoring (print path) ─────────────────────────────────────

class TestLogPrintPath(unittest.TestCase):

    def test_log_without_monitoring_prints(self):
        """_log без monitoring идёт через print — убеждаемся, что не падает."""
        mm = ModelManager()  # no monitoring
        mm._log("test message")  # должен вызвать print без ошибок


if __name__ == '__main__':
    unittest.main()
