# Покрытие: skills/skill_library.py
import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from skills.skill_library import Skill, SkillLibrary


class TestSkill(unittest.TestCase):

    def test_init(self):
        s = Skill('research', 'поиск инфо', 'search strategy', tags=['search'])
        self.assertEqual(s.name, 'research')
        self.assertEqual(s.use_count, 0)
        self.assertEqual(s.tags, ['search'])

    def test_success_rate_no_uses(self):
        s = Skill('x', 'd', 'strat')
        self.assertIsNone(s.success_rate)

    def test_success_rate_calculated(self):
        s = Skill('x', 'd', 'strat')
        s.record_use(True)
        s.record_use(True)
        s.record_use(False)
        self.assertAlmostEqual(s.success_rate, 0.67, places=2)

    def test_record_use(self):
        s = Skill('x', 'd', 'strat')
        s.record_use(True)
        self.assertEqual(s.use_count, 1)
        self.assertEqual(s.success_count, 1)
        s.record_use(False)
        self.assertEqual(s.use_count, 2)
        self.assertEqual(s.success_count, 1)

    def test_to_dict(self):
        s = Skill('test', 'desc', 'strat', tags=['a'], examples=['ex1'])
        d = s.to_dict()
        self.assertEqual(d['name'], 'test')
        self.assertEqual(d['tags'], ['a'])
        self.assertIn('success_rate', d)
        self.assertIn('created_at', d)


class TestSkillLibrary(unittest.TestCase):

    def _make(self, **kw):
        defaults = dict(
            cognitive_core=None,
            knowledge_system=None,
            monitoring=MagicMock(),
        )
        defaults.update(kw)
        with patch.object(SkillLibrary, '_load_from_disk'):
            lib = SkillLibrary(**defaults)
        return lib

    # ── register / update / remove ────────────────────────────────────────
    def test_register(self):
        lib = self._make()
        s = lib.register('new_skill', 'desc', 'strat', tags=['t'])
        self.assertIsInstance(s, Skill)
        self.assertIn('new_skill', lib._skills)

    def test_register_with_knowledge(self):
        ks = MagicMock()
        lib = self._make(knowledge_system=ks)
        lib.register('k_skill', 'desc', 'strat')
        ks.store_long_term.assert_called()

    def test_update(self):
        lib = self._make()
        lib.register('upd', 'old', 'old_strat')
        lib.update('upd', strategy='new_strat', description='new')
        self.assertEqual(lib._skills['upd'].strategy, 'new_strat')

    def test_update_missing_raises(self):
        lib = self._make()
        with self.assertRaises(KeyError):
            lib.update('nonexistent', strategy='x')

    def test_remove(self):
        lib = self._make()
        lib.register('rem', 'd', 's')
        lib.remove('rem')
        self.assertNotIn('rem', lib._skills)

    def test_remove_nonexistent(self):
        lib = self._make()
        lib.remove('nonexistent')  # should not raise

    # ── find ──────────────────────────────────────────────────────────────
    def test_find_empty_query(self):
        lib = self._make()
        self.assertEqual(lib.find(''), [])

    def test_find_matches(self):
        lib = self._make()
        lib.register('data_analysis', 'анализ данных из CSV', 'analyse strat',
                      tags=['analysis', 'data'])
        results = lib.find('анализ данных')
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].name, 'data_analysis')

    def test_find_no_match(self):
        lib = self._make()
        lib.register('xyz_unique', 'completely unrelated', 'strat',
                      tags=['zzzzz'])
        results = lib.find('quantum blockchain hyperledger')
        self.assertEqual(results, [])

    def test_find_top_k(self):
        lib = self._make()
        for i in range(5):
            lib.register(f'skill_{i}', f'writing content article #{i}',
                          'write strat', tags=['writing'])
        results = lib.find('writing content', top_k=2)
        self.assertLessEqual(len(results), 2)

    # ── find_by_tag ───────────────────────────────────────────────────────
    def test_find_by_tag(self):
        lib = self._make()
        lib.register('a', 'desc', 's', tags=['unique_xyz_tag'])
        lib.register('b', 'desc', 's', tags=['other_tag'])
        self.assertEqual(len(lib.find_by_tag('unique_xyz_tag')), 1)

    # ── get / list_all ────────────────────────────────────────────────────
    def test_get(self):
        lib = self._make()
        lib.register('g', 'desc', 's')
        self.assertIsNotNone(lib.get('g'))
        self.assertIsNone(lib.get('missing'))

    def test_list_all(self):
        lib = self._make()
        initial = len(lib.list_all())
        lib.register('extra', 'd', 's')
        self.assertEqual(len(lib.list_all()), initial + 1)

    # ── get_training_candidates ───────────────────────────────────────────
    def test_get_training_candidates(self):
        lib = self._make()
        lib.register('fresh', 'd', 's')
        lib.register('used', 'd', 's')
        lib._skills['used'].record_use(True)
        candidates = lib.get_training_candidates(limit=5)
        self.assertIsInstance(candidates, list)

    def test_get_training_candidates_no_limit(self):
        lib = self._make()
        candidates = lib.get_training_candidates()
        self.assertIsInstance(candidates, list)

    # ── apply ─────────────────────────────────────────────────────────────
    def test_apply_missing(self):
        lib = self._make()
        self.assertIsNone(lib.apply('nonexistent', 'task'))

    def test_apply_no_core(self):
        lib = self._make()
        lib.register('simple', 'desc', 'do this')
        result = lib.apply('simple', 'my task')
        self.assertEqual(result, 'do this')
        self.assertEqual(lib._skills['simple'].use_count, 1)

    def test_apply_with_core(self):
        cc = MagicMock()
        cc.reasoning.return_value = 'applied result'
        lib = self._make(cognitive_core=cc)
        lib.register('smart', 'desc', 'strat')
        result = lib.apply('smart', 'task')
        self.assertEqual(result, 'applied result')

    # ── learn_from_experience ─────────────────────────────────────────────
    def test_learn_failure_returns_none(self):
        lib = self._make()
        self.assertIsNone(lib.learn_from_experience('t', 's', success=False))

    def test_learn_no_core(self):
        lib = self._make()
        s = lib.learn_from_experience('task', 'solution', success=True)
        self.assertIsNotNone(s)
        self.assertIn(s.name, lib._skills)

    def test_learn_with_core(self):
        cc = MagicMock()
        cc.reasoning.return_value = (
            "НАЗВАНИЕ: auto_skill\n"
            "ОПИСАНИЕ: когда нужно что-то\n"
            "СТРАТЕГИЯ: делай вот так"
        )
        lib = self._make(cognitive_core=cc)
        s = lib.learn_from_experience('task', 'solution', True, tags=['auto'])
        self.assertIsNotNone(s)

    def test_learn_with_core_bad_format(self):
        cc = MagicMock()
        cc.reasoning.return_value = "just a plain string without format"
        lib = self._make(cognitive_core=cc)
        s = lib.learn_from_experience('task', 'sol', True)
        self.assertIsNotNone(s)

    # ── compose ───────────────────────────────────────────────────────────
    def test_compose(self):
        lib = self._make()
        lib.register('p1', 'part 1', 'step1', tags=['a'])
        lib.register('p2', 'part 2', 'step2', tags=['b'])
        composed = lib.compose(['p1', 'p2'], 'combo')
        self.assertIn('composed', composed.tags)
        self.assertIn('step1', composed.strategy)
        self.assertIn('step2', composed.strategy)

    def test_compose_missing_part(self):
        lib = self._make()
        lib.register('only', 'd', 's')
        composed = lib.compose(['only', 'nonexistent'], 'partial')
        self.assertIn('partial', lib._skills)

    # ── top_skills / summary ──────────────────────────────────────────────
    def test_top_skills(self):
        lib = self._make()
        lib.register('pop', 'd', 's')
        lib._skills['pop'].use_count = 100
        lib.register('rare', 'd', 's')
        top = lib.top_skills(n=1)
        self.assertEqual(top[0]['name'], 'pop')

    def test_summary(self):
        lib = self._make()
        lib.register('s1', 'd', 's', tags=['x'])
        s = lib.summary()
        self.assertIn('total', s)
        self.assertIn('by_tag', s)
        self.assertIn('avg_success_rate', s)

    # ── static helpers ────────────────────────────────────────────────────
    def test_significant_words(self):
        words = SkillLibrary._significant_words('a bb анализ данных')
        self.assertIn('анализ', words)
        self.assertIn('данных', words)
        self.assertNotIn('a', words)
        self.assertNotIn('bb', words)

    def test_jaccard(self):
        self.assertEqual(SkillLibrary._jaccard(set(), {'a'}), 0.0)
        self.assertEqual(SkillLibrary._jaccard({'a'}, set()), 0.0)
        self.assertAlmostEqual(SkillLibrary._jaccard({'a', 'b'}, {'a', 'b'}), 1.0)
        self.assertAlmostEqual(SkillLibrary._jaccard({'a', 'b'}, {'a', 'c'}), 1/3)

    # ── _load_defaults ────────────────────────────────────────────────────
    def test_defaults_loaded(self):
        lib = self._make()
        self.assertGreater(len(lib._skills), 0)


if __name__ == '__main__':
    unittest.main()
