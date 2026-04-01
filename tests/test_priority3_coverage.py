"""
Priority-3 coverage: social/*, reflection/*, learning/*.
"""
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. social/emotional_state.py
# ═══════════════════════════════════════════════════════════════════════════
from social.emotional_state import EmotionalState, Mood


class TestMood(unittest.TestCase):
    def test_enum_values(self):
        self.assertIsNotNone(Mood.JOYFUL)
        self.assertIsNotNone(Mood.WARM)
        self.assertIsNotNone(Mood.NEUTRAL)
        self.assertIsNotNone(Mood.FOCUSED)
        self.assertIsNotNone(Mood.CONCERNED)
        self.assertIsNotNone(Mood.APOLOGETIC)


class TestEmotionalState(unittest.TestCase):
    def test_initial_mood(self):
        es = EmotionalState()
        self.assertEqual(es.mood, Mood.WARM)

    def test_update_from_positive_tone(self):
        es = EmotionalState()
        result = es.update_from_user_tone('positive')
        self.assertIsInstance(result, Mood)

    def test_update_from_negative_tone(self):
        es = EmotionalState()
        es.update_from_user_tone('negative')
        # Valence should decrease
        self.assertLessEqual(es.valence, 0.1)

    def test_update_from_neutral_tone(self):
        es = EmotionalState()
        result = es.update_from_user_tone('neutral')
        self.assertIsInstance(result, Mood)

    def test_record_agent_error_single(self):
        es = EmotionalState()
        es.record_agent_error()
        self.assertLessEqual(es.valence, 0.0)

    def test_record_agent_error_streak(self):
        es = EmotionalState()
        es.record_agent_error()
        es.record_agent_error()
        es.record_agent_error()
        # After streak >= 2, should go APOLOGETIC
        self.assertEqual(es.mood, Mood.APOLOGETIC)

    def test_record_agent_success(self):
        es = EmotionalState()
        es.record_agent_error()
        es.record_agent_error()
        es.record_agent_error()
        es.record_agent_success()
        self.assertNotEqual(es.mood, Mood.APOLOGETIC)

    def test_get_mood_directive(self):
        es = EmotionalState()
        d = es.get_mood_directive()
        self.assertIsInstance(d, str)
        self.assertGreater(len(d), 0)

    def test_to_dict_from_dict(self):
        es = EmotionalState()
        es.update_from_user_tone('positive')
        d = es.to_dict()
        es2 = EmotionalState.from_dict(d)
        self.assertEqual(es2.mood, es.mood)

    def test_from_dict_invalid_mood(self):
        d = {'mood': 'NONEXISTENT', 'last_update': time.time(),
             'interaction_valence': 0, 'error_streak': 0}
        es = EmotionalState.from_dict(d)
        self.assertEqual(es.mood, Mood.WARM)  # fallback

    def test_decay_after_timeout(self):
        es = EmotionalState()
        es.update_from_user_tone('positive')
        # Force old timestamp
        es._last_update = time.time() - 3600
        directive = es.get_mood_directive()
        self.assertIsInstance(directive, str)


# ═══════════════════════════════════════════════════════════════════════════
# 2. social/social_model.py
# ═══════════════════════════════════════════════════════════════════════════
from social.social_model import (
    SocialInteractionModel, SocialActor, ConversationContext,
    RelationshipType, CommunicationStyle
)


class TestSocialActor(unittest.TestCase):
    def test_create(self):
        a = SocialActor('user1', 'Alice', RelationshipType.USER)
        self.assertEqual(a.actor_id, 'user1')

    def test_record_interaction(self):
        a = SocialActor('user1', 'Alice')
        a.record_interaction()
        self.assertGreaterEqual(a.interaction_count, 1)

    def test_to_dict(self):
        a = SocialActor('user1', 'Alice', RelationshipType.ADMIN)
        d = a.to_dict()
        self.assertEqual(d['actor_id'], 'user1')


class TestConversationContext(unittest.TestCase):
    def test_add_and_last_n(self):
        c = ConversationContext('user1', 'coding')
        c.add_message('user', 'hello')
        c.add_message('agent', 'hi there')
        msgs = c.last_n(5)
        self.assertEqual(len(msgs), 2)


class TestSocialInteractionModel(unittest.TestCase):
    def setUp(self):
        self.sim = SocialInteractionModel()

    def test_default_actors(self):
        system = self.sim.get_actor('system')
        user = self.sim.get_actor('user')
        self.assertIsNotNone(system)
        self.assertIsNotNone(user)

    def test_register_actor(self):
        a = self.sim.register_actor('dev1', 'Developer', RelationshipType.COLLEAGUE)
        self.assertEqual(a.name, 'Developer')
        self.assertIsNotNone(self.sim.get_actor('dev1'))

    def test_update_trust(self):
        self.sim.register_actor('t1', 'Test')
        self.sim.update_trust('t1', 1)
        actor = self.sim.get_actor('t1')
        self.assertIsNotNone(actor)

    def test_detect_tone_positive(self):
        tone = self.sim.detect_tone('Спасибо большое! Отлично сделано! Замечательно!')
        self.assertIsInstance(tone, str)

    def test_detect_tone_negative(self):
        tone = self.sim.detect_tone('Это ужасно! Плохо! Неправильно! Ошибка!')
        self.assertIsInstance(tone, str)

    def test_detect_tone_neutral(self):
        tone = self.sim.detect_tone('Просто текст без эмоций.')
        self.assertIsInstance(tone, str)

    def test_start_conversation(self):
        ctx = self.sim.start_conversation('user', 'coding help')
        self.assertIsInstance(ctx, ConversationContext)

    def test_add_to_conversation(self):
        self.sim.start_conversation('user', 'topic')
        self.sim.add_to_conversation('user', 'user', 'hello')
        self.sim.add_to_conversation('user', 'agent', 'hi')
        ctx = self.sim.get_conversation('user')
        self.assertEqual(len(ctx.last_n(10)), 2)

    def test_add_to_conversation_auto_create(self):
        self.sim.add_to_conversation('new_user', 'user', 'hello')
        ctx = self.sim.get_conversation('new_user')
        self.assertIsNotNone(ctx)

    def test_detect_style_concise(self):
        self.sim.register_actor('s1', 'Short')
        sample = 'ok. done. yes. fix it.'
        self.sim.detect_style('s1', sample)

    def test_detect_style_detailed(self):
        self.sim.register_actor('s2', 'Verbose')
        sample = ('Мне нужно подробное объяснение каждого шага процесса '
                  'с примерами и деталями реализации. '
                  'Пожалуйста, включите все возможные варианты и '
                  'альтернативные подходы к решению задачи.')
        self.sim.detect_style('s2', sample)

    def test_suggest_response_tone(self):
        self.sim.start_conversation('user', 'help')
        tone = self.sim.suggest_response_tone('user')
        self.assertIsInstance(tone, str)

    def test_list_actors(self):
        actors = self.sim.list_actors()
        self.assertGreaterEqual(len(actors), 2)

    def test_summary(self):
        s = self.sim.summary()
        self.assertIn('actors', s)

    def test_adapt_response_no_core(self):
        result = self.sim.adapt_response('Hello world', 'user')
        self.assertIsInstance(result, str)

    def test_adapt_response_with_core(self):
        core = SimpleNamespace(reasoning=MagicMock(return_value='Привет, мир!'))
        sim = SocialInteractionModel(cognitive_core=core)
        result = sim.adapt_response('Hello', 'user')
        self.assertIsInstance(result, str)


# ═══════════════════════════════════════════════════════════════════════════
# 3. reflection/reflection_system.py
# ═══════════════════════════════════════════════════════════════════════════
from reflection.reflection_system import ReflectionSystem


class TestReflectionSystem(unittest.TestCase):
    def setUp(self):
        self.rs = ReflectionSystem()

    def test_reflect_basic(self):
        r = self.rs.reflect('write code', {'status': 'done', 'output': 'ok'})
        self.assertIn('goal_achieved', r)
        self.assertIsInstance(r.get('analysis'), str)

    def test_reflect_with_context(self):
        r = self.rs.reflect('deploy', {'status': 'failed'}, context={'env': 'prod'})
        self.assertIn('goal_achieved', r)

    def test_reflect_stores_reflection(self):
        self.rs.reflect('task1', {'status': 'done'})
        self.rs.reflect('task2', {'status': 'failed'})
        refs = self.rs.get_reflections()
        self.assertGreaterEqual(len(refs), 2)

    def test_reflect_with_core(self):
        core = SimpleNamespace(
            reasoning=MagicMock(return_value='Analysis: good result. Цель достигнута. Success.'),
            decision_making=MagicMock(return_value='цель достигнута'),
            strategy_generator=MagicMock(return_value='Рекомендация: продолжать'))
        rs = ReflectionSystem(cognitive_core=core)
        r = rs.reflect('build module', {'status': 'success', 'output': 'module built'})
        self.assertIn('analysis', r)

    def test_analyse_error(self):
        r = self.rs.analyse_error(ValueError('bad value'), 'parsing input')
        self.assertIn('error_type', r)
        self.assertIn('error_message', r)

    def test_analyse_error_with_core(self):
        core = SimpleNamespace(reasoning=MagicMock(return_value='Root cause: invalid data'))
        rs = ReflectionSystem(cognitive_core=core)
        r = rs.analyse_error(RuntimeError('crash'), 'running task')
        self.assertIn('analysis', r)

    def test_evaluate_quality(self):
        q = self.rs.evaluate_quality(
            'The agent successfully wrote a Python module with tests',
            ['Python code', 'tests included']
        )
        self.assertIn('score', q)

    def test_evaluate_quality_no_match(self):
        q = self.rs.evaluate_quality(
            'Random unrelated content about weather',
            ['Python code', 'tests', 'documentation']
        )
        self.assertIn('score', q)

    def test_add_and_get_insights(self):
        self.rs.add_insight('Always validate input')
        self.rs.add_insight('Use async for IO')
        insights = self.rs.get_insights()
        self.assertEqual(len(insights), 2)

    def test_get_reflections_last_n(self):
        for i in range(5):
            self.rs.reflect(f'task{i}', {'status': 'done'})
        last2 = self.rs.get_reflections(last_n=2)
        self.assertEqual(len(last2), 2)

    def test_summary(self):
        self.rs.reflect('ok', {'status': 'done'})
        self.rs.reflect('fail', {'status': 'failed'})
        s = self.rs.summary()
        self.assertIn('total_reflections', s)


# ═══════════════════════════════════════════════════════════════════════════
# 4. learning/experience_replay.py
# ═══════════════════════════════════════════════════════════════════════════
from learning.experience_replay import ExperienceReplay, Episode


class TestEpisode(unittest.TestCase):
    def test_create(self):
        e = Episode('ep1', 'write code', ['write file'], 'file written', True)
        self.assertTrue(e.success)
        self.assertEqual(e.goal, 'write code')

    def test_to_dict(self):
        e = Episode('ep1', 'goal', ['a1'], 'ok', True)
        d = e.to_dict()
        self.assertEqual(d['episode_id'], 'ep1')
        self.assertTrue(d['success'])


class TestExperienceReplay(unittest.TestCase):
    def setUp(self):
        self.er = ExperienceReplay(buffer_size=100)

    def test_add(self):
        ep = self.er.add('build module', ['create file'], 'done', True)
        self.assertIsInstance(ep, Episode)
        self.assertEqual(self.er.size, 1)

    def test_sample_random(self):
        for i in range(10):
            self.er.add(f'task{i}', [f'act{i}'], f'out{i}', i % 2 == 0)
        sample = self.er.sample_random(3)
        self.assertEqual(len(sample), 3)

    def test_sample_failures(self):
        self.er.add('ok', ['a'], 'done', True)
        self.er.add('bad1', ['a'], 'error', False)
        self.er.add('bad2', ['a'], 'crash', False)
        fails = self.er.sample_failures(5)
        self.assertTrue(all(not ep.success for ep in fails))

    def test_sample_successes(self):
        self.er.add('good1', ['a'], 'done', True)
        self.er.add('good2', ['a'], 'ok', True)
        self.er.add('bad', ['a'], 'error', False)
        succ = self.er.sample_successes(5)
        self.assertTrue(all(ep.success for ep in succ))

    def test_sample_by_goal(self):
        self.er.add('build python module', ['a'], 'done', True)
        self.er.add('write report', ['a'], 'done', True)
        self.er.add('build javascript module', ['a'], 'done', True)
        matches = self.er.sample_by_goal('build')
        self.assertGreaterEqual(len(matches), 2)

    def test_replay_no_core(self):
        self.er.add('task1', ['a'], 'done', True)
        self.er.add('task2', ['a'], 'err', False)
        results = self.er.replay(n=2)
        self.assertIsInstance(results, list)

    def test_replay_with_episodes(self):
        ep = self.er.add('task', ['act'], 'result', True)
        results = self.er.replay(episodes=[ep])
        self.assertIsInstance(results, list)

    def test_replay_all(self):
        for i in range(5):
            self.er.add(f'task{i}', ['a'], 'out', i % 2 == 0)
        results = self.er.replay_all(focus='failures')
        self.assertIsInstance(results, list)

    def test_buffer_limit(self):
        er = ExperienceReplay(buffer_size=5)
        for i in range(10):
            er.add(f'task{i}', ['a'], 'out', True)
        self.assertLessEqual(er.size, 5)

    def test_summary(self):
        self.er.add('t1', ['a'], 'ok', True)
        self.er.add('t2', ['a'], 'err', False)
        s = self.er.summary()
        self.assertIn('buffer_size', s)

    def test_get_patterns_empty(self):
        patterns = self.er.get_patterns()
        self.assertEqual(patterns, [])


# ═══════════════════════════════════════════════════════════════════════════
# 5. learning/learning_system.py
# ═══════════════════════════════════════════════════════════════════════════
from learning.learning_system import LearningSystem


class TestLearningSystem(unittest.TestCase):
    def setUp(self):
        self.ls = LearningSystem()

    def test_learn_from_article(self):
        content = ('Python is a versatile programming language used for web development, '
                   'data science, machine learning, and automation. '
                   'It has a large ecosystem of libraries and frameworks. '
                   'Django and Flask are popular web frameworks. '
                   'NumPy and pandas are used for data processing.')
        r = self.ls.learn_from(content, source_type='article', source_name='python_intro')
        # topic/key_terms are nested under 'extracted'
        extracted = r.get('extracted', r)
        self.assertIn('topic', extracted)
        self.assertIn('key_terms', extracted)

    def test_learn_from_short_text(self):
        r = self.ls.learn_from('Short text.', source_type='article')
        self.assertIsInstance(r, dict)

    def test_enqueue_and_process(self):
        self.ls.enqueue('article', 'test_source',
                        fetch_fn=lambda: 'Content about AI and machine learning engineering.')
        results = self.ls.process_queue()
        self.assertGreaterEqual(len(results), 1)

    def test_enqueue_no_fetch_fn(self):
        self.ls.enqueue('article', 'unfetchable')
        results = self.ls.process_queue()
        self.assertIsInstance(results, list)

    def test_get_learned(self):
        self.ls.learn_from('Content about AI' * 5, source_type='article')
        self.ls.learn_from('Code snippet' * 5, source_type='code')
        all_items = self.ls.get_learned()
        self.assertGreaterEqual(len(all_items), 2)
        articles = self.ls.get_learned(source_type='article')
        self.assertTrue(all(i.get('source_type') == 'article' for i in articles))

    def test_get_stats(self):
        self.ls.learn_from('Some content about programming', source_type='article')
        stats = self.ls.get_stats()
        self.assertIn('total_learned', stats)
        self.assertGreaterEqual(stats['total_learned'], 1)

    def test_process_queue_with_error(self):
        def bad_fetch():
            raise RuntimeError('network error')
        self.ls.enqueue('web', 'broken', fetch_fn=bad_fetch)
        # process_queue may propagate or swallow; either way should not crash tests
        try:
            results = self.ls.process_queue()
            self.assertIsInstance(results, list)
        except RuntimeError:
            pass  # acceptable — error propagated from fetch_fn

    def test_learn_with_knowledge_system(self):
        ks = SimpleNamespace(store_long_term=MagicMock())
        ls = LearningSystem(knowledge_system=ks)
        ls.learn_from('Long content about databases and SQL and storage systems' * 3,
                      source_type='article')
        # Should attempt to store in knowledge
        ks.store_long_term.assert_called()


if __name__ == '__main__':
    unittest.main()
