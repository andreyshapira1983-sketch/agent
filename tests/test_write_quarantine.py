# Tests: WriteQuarantine — formal_contracts_spec §9
# Covers: quarantine submit, promote, reject, auto_verify_batch,
#         trusted source bypass, integration with KnowledgeSystem

import unittest
from unittest.mock import MagicMock, patch

from knowledge.write_quarantine import (
    WriteQuarantine,
    QuarantineRecord,
    TRUSTED_SOURCES,
)
from knowledge.knowledge_system import KnowledgeSystem


# ═══════════════════════════════════════════════════════════════════════════════
# QuarantineRecord
# ═══════════════════════════════════════════════════════════════════════════════

class TestQuarantineRecord(unittest.TestCase):

    def test_to_dict(self):
        rec = QuarantineRecord(
            key='fact_1', value='Earth is round',
            provenance={'source': 'web', 'trust': 0.5},
            memory_type='long_term',
        )
        d = rec.to_dict()
        self.assertEqual(d['key'], 'fact_1')
        self.assertEqual(d['value'], 'Earth is round')
        self.assertEqual(d['provenance']['source'], 'web')
        self.assertEqual(d['memory_type'], 'long_term')

    def test_defaults(self):
        rec = QuarantineRecord(
            key='k', value='v',
            provenance={'source': 'web'},
        )
        self.assertEqual(rec.review_count, 0)
        self.assertEqual(rec.rejection_reason, '')
        self.assertEqual(rec.memory_type, 'long_term')


# ═══════════════════════════════════════════════════════════════════════════════
# Submit: untrusted → quarantine
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitUntrusted(unittest.TestCase):

    def setUp(self):
        self.ks = KnowledgeSystem()
        self.q = WriteQuarantine(knowledge_system=self.ks)

    def test_web_source_quarantined(self):
        """§9: unverified record from 'web' goes to quarantine, NOT to long_term."""
        result = self.q.submit(
            'claim_1', 'some fact',
            provenance={'source': 'web', 'trust': 0.4},
        )
        self.assertTrue(result)
        self.assertEqual(self.q.pending_count(), 1)
        self.assertIsNone(self.ks.get_long_term('claim_1'))

    def test_external_source_quarantined(self):
        result = self.q.submit(
            'ext_1', 'external data',
            provenance={'source': 'external', 'trust': 0.3},
        )
        self.assertTrue(result)
        self.assertEqual(self.q.pending_count(), 1)

    def test_generated_source_quarantined(self):
        result = self.q.submit(
            'gen_1', 'LLM output',
            provenance={'source': 'generated', 'trust': 0.5},
        )
        self.assertTrue(result)
        self.assertIsNotNone(self.q.get_pending('gen_1'))

    def test_unknown_source_quarantined(self):
        result = self.q.submit(
            'unk_1', 'mystery data',
            provenance={'source': 'unknown', 'trust': 0.1},
        )
        self.assertTrue(result)
        self.assertEqual(self.q.pending_count(), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Submit: trusted → direct write (no quarantine)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitTrusted(unittest.TestCase):

    def setUp(self):
        self.ks = KnowledgeSystem()
        self.q = WriteQuarantine(knowledge_system=self.ks)

    def test_internal_source_direct(self):
        """Trusted source 'internal' bypasses quarantine."""
        self.q.submit(
            'int_1', 'internal fact',
            provenance={'source': 'internal', 'trust': 0.9},
        )
        self.assertEqual(self.q.pending_count(), 0)
        self.assertEqual(self.ks.get_long_term('int_1'), 'internal fact')

    def test_user_source_direct(self):
        self.q.submit(
            'usr_1', 'user said',
            provenance={'source': 'user', 'trust': 1.0},
        )
        self.assertEqual(self.q.pending_count(), 0)
        self.assertEqual(self.ks.get_long_term('usr_1'), 'user said')

    def test_reflection_source_direct(self):
        self.q.submit(
            'ref_1', 'self-insight',
            provenance={'source': 'reflection', 'trust': 0.8},
        )
        self.assertEqual(self.q.pending_count(), 0)
        self.assertEqual(self.ks.get_long_term('ref_1'), 'self-insight')

    def test_semantic_direct(self):
        self.q.submit(
            'Python', 'A programming language',
            provenance={'source': 'internal'},
            memory_type='semantic',
        )
        self.assertEqual(self.q.pending_count(), 0)
        self.assertEqual(self.ks.get_semantic('Python'), 'A programming language')


# ═══════════════════════════════════════════════════════════════════════════════
# Promote
# ═══════════════════════════════════════════════════════════════════════════════

class TestPromote(unittest.TestCase):

    def setUp(self):
        self.ks = KnowledgeSystem()
        self.q = WriteQuarantine(knowledge_system=self.ks)

    def test_promote_writes_to_long_term(self):
        self.q.submit('web_1', 'from web', provenance={'source': 'web', 'trust': 0.4})
        self.assertIsNone(self.ks.get_long_term('web_1'))

        result = self.q.promote('web_1')
        self.assertTrue(result)
        self.assertEqual(self.ks.get_long_term('web_1'), 'from web')
        self.assertEqual(self.q.pending_count(), 0)

    def test_promote_sets_verified(self):
        self.q.submit('web_2', 'info', provenance={'source': 'web', 'trust': 0.5})
        self.q.promote('web_2')
        prov = self.ks.get_provenance('web_2')
        self.assertIsNotNone(prov)
        assert prov is not None
        self.assertTrue(prov.get('verified'))

    def test_promote_semantic(self):
        self.q.submit(
            'Rust', 'Systems language',
            provenance={'source': 'generated'},
            memory_type='semantic',
        )
        self.q.promote('Rust')
        self.assertEqual(self.ks.get_semantic('Rust'), 'Systems language')

    def test_promote_nonexistent_returns_false(self):
        self.assertFalse(self.q.promote('nonexistent'))

    def test_promote_logged(self):
        self.q.submit('x', 'val', provenance={'source': 'web'})
        self.q.promote('x')
        log = self.q.get_promoted_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]['key'], 'x')


# ═══════════════════════════════════════════════════════════════════════════════
# Reject
# ═══════════════════════════════════════════════════════════════════════════════

class TestReject(unittest.TestCase):

    def setUp(self):
        self.ks = KnowledgeSystem()
        self.q = WriteQuarantine(knowledge_system=self.ks)

    def test_reject_removes_from_pending(self):
        self.q.submit('bad_1', 'wrong fact', provenance={'source': 'web'})
        result = self.q.reject('bad_1', reason='contradicts known facts')
        self.assertTrue(result)
        self.assertEqual(self.q.pending_count(), 0)
        self.assertIsNone(self.ks.get_long_term('bad_1'))

    def test_reject_nonexistent_returns_false(self):
        self.assertFalse(self.q.reject('nonexistent'))

    def test_reject_logged(self):
        self.q.submit('bad_2', 'x', provenance={'source': 'external'})
        self.q.reject('bad_2', reason='test reason')
        log = self.q.get_rejected_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]['reason'], 'test reason')

    def test_reject_does_not_write_to_memory(self):
        self.q.submit('bad_3', 'harmful', provenance={'source': 'web'})
        self.q.reject('bad_3')
        self.assertIsNone(self.ks.get_long_term('bad_3'))


# ═══════════════════════════════════════════════════════════════════════════════
# list_pending / get_pending
# ═══════════════════════════════════════════════════════════════════════════════

class TestListPending(unittest.TestCase):

    def setUp(self):
        self.q = WriteQuarantine(knowledge_system=KnowledgeSystem())

    def test_list_pending_empty(self):
        self.assertEqual(self.q.list_pending(), [])

    def test_list_pending_multiple(self):
        self.q.submit('a', '1', provenance={'source': 'web'})
        self.q.submit('b', '2', provenance={'source': 'external'})
        pending = self.q.list_pending()
        self.assertEqual(len(pending), 2)
        keys = {r.key for r in pending}
        self.assertEqual(keys, {'a', 'b'})

    def test_get_pending_exists(self):
        self.q.submit('k1', 'v1', provenance={'source': 'web'})
        rec = self.q.get_pending('k1')
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec.value, 'v1')

    def test_get_pending_missing(self):
        self.assertIsNone(self.q.get_pending('missing'))


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-verify batch
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutoVerifyBatch(unittest.TestCase):

    def setUp(self):
        self.ks = KnowledgeSystem()
        self.verifier = MagicMock()
        self.q = WriteQuarantine(knowledge_system=self.ks, verifier=self.verifier)

    def test_no_verifier_returns_error(self):
        q = WriteQuarantine(knowledge_system=self.ks, verifier=None)
        q.submit('k', 'v', provenance={'source': 'web'})
        stats = q.auto_verify_batch()
        self.assertEqual(stats['error'], 'no_verifier')

    def test_verified_promotes(self):
        self.q.submit('fact_1', 'true fact', provenance={'source': 'web'})
        result = MagicMock()
        result.status = 'verified'
        self.verifier.verify.return_value = result

        stats = self.q.auto_verify_batch()
        self.assertEqual(stats['promoted'], 1)
        self.assertEqual(self.q.pending_count(), 0)
        self.assertEqual(self.ks.get_long_term('fact_1'), 'true fact')

    def test_contradicted_rejects(self):
        self.q.submit('lie_1', 'false claim', provenance={'source': 'web'})
        result = MagicMock()
        result.status = 'contradicted'
        self.verifier.verify.return_value = result

        stats = self.q.auto_verify_batch()
        self.assertEqual(stats['rejected'], 1)
        self.assertEqual(self.q.pending_count(), 0)
        self.assertIsNone(self.ks.get_long_term('lie_1'))

    def test_inconclusive_skips(self):
        self.q.submit('maybe_1', 'uncertain', provenance={'source': 'web'})
        result = MagicMock()
        result.status = 'inconclusive'
        self.verifier.verify.return_value = result

        stats = self.q.auto_verify_batch()
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(self.q.pending_count(), 1)
        rec = self.q.get_pending('maybe_1')
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec.review_count, 1)

    def test_exception_in_verify_skips(self):
        self.q.submit('err_1', 'val', provenance={'source': 'web'})
        self.verifier.verify.side_effect = RuntimeError('LLM down')

        stats = self.q.auto_verify_batch()
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(self.q.pending_count(), 1)

    def test_mixed_batch(self):
        self.q.submit('a', '1', provenance={'source': 'web'})
        self.q.submit('b', '2', provenance={'source': 'external'})
        self.q.submit('c', '3', provenance={'source': 'generated'})

        results = iter([
            MagicMock(status='verified'),
            MagicMock(status='contradicted'),
            MagicMock(status='inconclusive'),
        ])
        self.verifier.verify.side_effect = lambda *a, **kw: next(results)

        stats = self.q.auto_verify_batch()
        self.assertEqual(stats['promoted'], 1)
        self.assertEqual(stats['rejected'], 1)
        self.assertEqual(stats['skipped'], 1)
        self.assertEqual(self.q.pending_count(), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# TRUSTED_SOURCES constant
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrustedSources(unittest.TestCase):

    def test_contains_expected(self):
        self.assertIn('internal', TRUSTED_SOURCES)
        self.assertIn('user', TRUSTED_SOURCES)
        self.assertIn('reflection', TRUSTED_SOURCES)

    def test_web_not_trusted(self):
        self.assertNotIn('web', TRUSTED_SOURCES)
        self.assertNotIn('external', TRUSTED_SOURCES)
        self.assertNotIn('generated', TRUSTED_SOURCES)
        self.assertNotIn('unknown', TRUSTED_SOURCES)


# ═══════════════════════════════════════════════════════════════════════════════
# Thread safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrency(unittest.TestCase):

    def test_concurrent_submits(self):
        import threading
        ks = KnowledgeSystem()
        q = WriteQuarantine(knowledge_system=ks)
        errors = []

        def submit_batch(start):
            try:
                for i in range(20):
                    q.submit(f'k_{start}_{i}', f'v{i}',
                             provenance={'source': 'web'})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submit_batch, args=(t,))
                   for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        self.assertEqual(q.pending_count(), 100)


# ═══════════════════════════════════════════════════════════════════════════════
# DI: set_knowledge_system / set_verifier
# ═══════════════════════════════════════════════════════════════════════════════

class TestDelayedInjection(unittest.TestCase):

    def test_submit_without_ks_queues_but_does_not_crash(self):
        q = WriteQuarantine()
        result = q.submit('k', 'v', provenance={'source': 'web'})
        self.assertTrue(result)
        self.assertEqual(q.pending_count(), 1)

    def test_trusted_without_ks_returns_false(self):
        q = WriteQuarantine()
        result = q.submit('k', 'v', provenance={'source': 'internal'})
        self.assertFalse(result)

    def test_set_knowledge_system_then_promote(self):
        q = WriteQuarantine()
        q.submit('k', 'v', provenance={'source': 'web'})

        ks = KnowledgeSystem()
        q.set_knowledge_system(ks)
        q.promote('k')
        self.assertEqual(ks.get_long_term('k'), 'v')


if __name__ == '__main__':
    unittest.main()
