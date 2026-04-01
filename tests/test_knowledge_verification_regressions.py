import unittest

from knowledge.knowledge_verification import (
    KnowledgeVerificationSystem,
    VerificationResult,
    VerificationStatus,
)


class _DummyKnowledge:
    def __init__(self):
        self._long_term = {
            'fact:1': 'This is a long enough factual statement about a system behavior.'
        }
        self.deleted_keys = []
        self.stored_flags = {}

    def delete_long_term(self, key):
        self.deleted_keys.append(key)
        self._long_term.pop(key, None)

    def store_long_term(self, key, value, **_kwargs):
        self.stored_flags[key] = value
        self._long_term[key] = value


class KnowledgeVerificationRegressionTests(unittest.TestCase):
    def test_parse_result_non_numeric_confidence_is_safe(self):
        ver = KnowledgeVerificationSystem(knowledge_system=None, cognitive_core=None)

        status, confidence, evidence, contradiction, notes = ver._parse_result(
            'СТАТУС: verified\n'
            'УВЕРЕННОСТЬ: definitely-high\n'
            'ДОКАЗАТЕЛЬСТВО: source matched\n'
            'ПРОТИВОРЕЧИЕ: none\n'
            'ПРИМЕЧАНИЕ: test\n'
        )

        self.assertEqual(status, VerificationStatus.VERIFIED)
        self.assertEqual(confidence, 0.5)
        self.assertEqual(evidence, 'source matched')
        self.assertEqual(contradiction, 'none')
        self.assertEqual(notes, 'test')

    def test_verify_knowledge_base_soft_flags_instead_of_hard_delete(self):
        knowledge = _DummyKnowledge()
        ver = KnowledgeVerificationSystem(knowledge_system=knowledge, cognitive_core=None)

        def _fake_verify(claim, source=None, context=None):
            _ = claim, source, context
            return VerificationResult(
                claim='fake',
                status=VerificationStatus.FALSE,
                confidence=0.95,
                evidence=['e1'],
                contradictions=['c1'],
                notes='simulated false',
            )

        ver.verify = _fake_verify

        stats = ver.verify_knowledge_base(sample_size=1)

        self.assertEqual(stats['false'], 1)
        self.assertEqual(knowledge.deleted_keys, [])
        self.assertIn('verification_flag:fact:1', knowledge.stored_flags)


if __name__ == '__main__':
    unittest.main()
