"""Tests for llm/claude_backend.py — ClaudeClient."""
import time
from unittest.mock import patch, MagicMock, PropertyMock
import pytest


class TestClaudeClient:
    def _make_client(self, **kwargs):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        with patch('importlib.import_module', return_value=mock_anthropic):
            from llm.claude_backend import ClaudeClient
            client = ClaudeClient(api_key='test-key', **kwargs)
        client._mock_client = mock_client
        return client

    def test_init_defaults(self):
        c = self._make_client()
        assert c.model == 'claude-haiku-4-5'
        assert c.max_tokens == 2048
        assert c.temperature == 0.7
        assert c._total_tokens == 0
        assert c._total_cost == 0.0

    def test_init_custom(self):
        c = self._make_client(model='claude-opus-4-6', max_tokens=4096, temperature=0.5)
        assert c.model == 'claude-opus-4-6'
        assert c.max_tokens == 4096

    def test_init_import_error(self):
        with patch('importlib.import_module', side_effect=ImportError):
            from llm.claude_backend import ClaudeClient
            with pytest.raises(ImportError, match="anthropic"):
                ClaudeClient(api_key='test')

    def _make_response(self, text='Hello response', input_tokens=10, output_tokens=20):
        """Helper to create a properly mocked Anthropic response."""
        block = MagicMock()
        block.type = 'text'
        block.text = text
        resp = MagicMock()
        resp.content = [block]
        resp.usage.input_tokens = input_tokens
        resp.usage.output_tokens = output_tokens
        return resp

    def test_infer_basic(self):
        c = self._make_client()
        c._mock_client.messages.create.return_value = self._make_response('Hello response')

        result = c.infer('test prompt')
        assert 'Hello response' in result
        c._mock_client.messages.create.assert_called_once()

    def test_infer_with_context(self):
        c = self._make_client()
        c._mock_client.messages.create.return_value = self._make_response('with context')

        result = c.infer('test', context={'key': 'value'})
        assert result

    def test_infer_with_history(self):
        c = self._make_client()
        c._mock_client.messages.create.return_value = self._make_response('history response')

        history = [
            {'role': 'user', 'content': 'hi'},
            {'role': 'assistant', 'content': 'hello'},
        ]
        result = c.infer('next msg', history=history)
        assert result

    def test_infer_with_system(self):
        c = self._make_client()
        c._mock_client.messages.create.return_value = self._make_response('custom system')

        result = c.infer('prompt', system='Custom system prompt')
        assert result

    def test_infer_prompt_truncation(self):
        c = self._make_client()
        c._mock_client.messages.create.return_value = self._make_response('truncated')

        long_prompt = 'x' * 100_000
        result = c.infer(long_prompt)
        assert result

    def test_infer_budget_exceeded(self):
        bc = MagicMock()
        bc.gate.return_value = False
        bc.get_status.return_value = {'usd': {'spent': 10, 'limit': 5}}
        c = self._make_client(budget_control=bc)

        with pytest.raises(RuntimeError, match="Бюджет исчерпан"):
            c.infer('test')

    def test_infer_credit_exhausted_fallback(self):
        fallback = MagicMock()
        fallback.infer.return_value = 'fallback response'
        c = self._make_client(fallback_client=fallback)
        c._credit_exhausted = True
        c._credit_retry_after = time.time() + 9999

        result = c.infer('test')
        assert result == 'fallback response'

    def test_infer_credit_exhausted_no_fallback(self):
        c = self._make_client()
        c._credit_exhausted = True
        c._credit_retry_after = time.time() + 9999

        result = c.infer('test')
        assert result == ''

    def test_infer_credit_retry_after_expired(self):
        c = self._make_client()
        c._credit_exhausted = True
        c._credit_retry_after = time.time() - 1  # retry time passed

        c._mock_client.messages.create.return_value = self._make_response('recovered')

        c.infer('test')
        assert not c._credit_exhausted

    def test_infer_credit_retry_with_monitoring(self):
        mon = MagicMock()
        c = self._make_client(monitoring=mon)
        c._credit_exhausted = True
        c._credit_retry_after = time.time() - 1

        c._mock_client.messages.create.return_value = self._make_response('ok')

        c.infer('test')
        mon.info.assert_called()

    def test_pricing_keys(self):
        c = self._make_client()
        assert 'claude-haiku-4-5' in c._pricing
        assert 'claude-opus-4-6' in c._pricing
