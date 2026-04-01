import threading
import unittest
from unittest.mock import patch

from communication.telegram_bot import TelegramBot
from knowledge.vector_store import VectorStore


class TelegramBotRegressionTests(unittest.TestCase):
    def test_approval_resolution_sets_event_and_result(self):
        bot = TelegramBot(token='test-token', allowed_chat_ids=[1])
        approval_id = 'abc12345'
        event = threading.Event()

        with bot._approvals_lock:
            bot._pending_approvals[approval_id] = {
                'event': event,
                'result': False,
                'chat_ids': [1],
                'created_at': 0.0,
                'action_type': 'run',
            }

        bot._resolve_approval(approval_id, True)

        self.assertTrue(event.is_set())
        with bot._approvals_lock:
            self.assertTrue(bot._pending_approvals[approval_id]['result'])

    def test_polling_backoff_grows_on_error_and_resets_after_success(self):
        bot = TelegramBot(token='test-token', allowed_chat_ids=[1])
        bot._running = True

        state = {'calls': 0}

        def fake_get_updates():
            state['calls'] += 1
            if state['calls'] == 1:
                raise RuntimeError('transient polling failure')
            if state['calls'] >= 3:
                bot._running = False
            return []

        sleep_calls = []

        with patch.object(bot, '_get_updates', side_effect=fake_get_updates):
            with patch('communication.telegram_bot.time.sleep', side_effect=lambda sec: sleep_calls.append(sec)):
                bot._poll_loop()

        self.assertIn(1.0, sleep_calls)
        self.assertEqual(bot._poll_backoff, 1.0)


class VectorStoreRegressionTests(unittest.TestCase):
    def test_chromadb_search_failure_falls_back_to_tfidf(self):
        class BrokenBackend:
            def query(self, **kwargs):
                raise RuntimeError('chroma unavailable')

        store = VectorStore(use_chroma=False)
        store.add('doc1', 'hello ai world')
        store._backend_type = 'chromadb'
        store._backend = BrokenBackend()

        results = store.search('hello', n=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].doc.doc_id, 'doc1')

    def test_chromadb_search_with_empty_local_cache_uses_backend_and_caches(self):
        class FakeBackend:
            def __init__(self):
                self.last_kwargs = None

            def query(self, **kwargs):
                self.last_kwargs = kwargs
                return {
                    'ids': [['doc-x']],
                    'distances': [[0.2]],
                    'metadatas': [[{'source': 'chroma'}]],
                    'documents': [['external text from chroma']],
                }

        backend = FakeBackend()
        store = VectorStore(use_chroma=False)
        store._backend_type = 'chromadb'
        store._backend = backend

        results = store.search('external', n=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].doc.doc_id, 'doc-x')
        self.assertIsNotNone(backend.last_kwargs)
        self.assertEqual(backend.last_kwargs['n_results'], 1)
        self.assertIn('doc-x', store._docs)


if __name__ == '__main__':
    unittest.main()
