import tempfile
import threading
import unittest
from typing import cast

from core.persistent_brain import PersistentBrain
from knowledge.knowledge_system import KnowledgeSystem


class MemoryQualityRegressionTests(unittest.TestCase):
    def test_knowledge_short_token_query_ai(self):
        ks = KnowledgeSystem()
        ks.store_long_term('topic:ai', 'AI systems and planning')

        result = ks.get_relevant_knowledge('AI')

        self.assertIsNotNone(result)
        result_map = cast(dict, result)
        self.assertIn('long_term', result_map)
        self.assertIn('topic:ai', result_map['long_term'])

    def test_consolidate_uses_stable_key_for_same_value(self):
        ks = KnowledgeSystem()
        ks.add_short_term({'persist': True, 'value': 'same knowledge payload'})
        ks.add_short_term({'persist': True, 'value': 'same knowledge payload'})

        ks.consolidate()

        self.assertEqual(len(ks.long_term_items()), 1)
        key = ks.long_term_keys()[0]
        self.assertTrue(key.startswith('consolidated:'))

    def test_persistent_brain_concurrent_load_save_no_crash(self):
        errors = []

        with tempfile.TemporaryDirectory() as tmp:
            brain = PersistentBrain(data_dir=tmp)
            brain.save()

            def _save_loop():
                try:
                    for _ in range(20):
                        brain.save()
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            def _load_loop():
                try:
                    for _ in range(20):
                        brain.load()
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            t1 = threading.Thread(target=_save_loop)
            t2 = threading.Thread(target=_load_loop)
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
