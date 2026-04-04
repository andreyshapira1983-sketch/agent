"""Тесты для llm/local_backend.py — LocalNeuralBackend (in-process transformers)."""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
import llm.local_backend as lb_mod
from llm.local_backend import LocalNeuralBackend


class TestLocalNeuralBackendInit:
    def test_defaults(self):
        b = LocalNeuralBackend()
        assert "Qwen" in b.model or "qwen" in b.model.lower() or b.model  # has a model
        assert b.timeout == 90
        assert b._total_tokens == 0
        assert b._total_cost == 0.0
        assert b._model is None
        assert b._tokenizer is None
        assert b._unloaded_by_ram is False

    def test_custom_model(self):
        b = LocalNeuralBackend(model="test/model", timeout=30)
        assert b.model == "test/model"
        assert b.timeout == 30


class TestLocalNeuralBackendHealth:
    def test_health_no_transformers(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", False):
            b = LocalNeuralBackend()
            h = b.health()
            assert h["ok"] is False
            assert h["loaded"] is False
            assert "не установлены" in h["error"]

    def test_health_not_loaded_no_error(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", True):
            b = LocalNeuralBackend()
            b._model = None
            b._tokenizer = None
            b._load_error = ""
            b._unloaded_by_ram = False
            h = b.health()
            assert h["ok"] is True  # model can be loaded lazily
            assert h["loaded"] is False

    def test_health_loaded(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", True):
            b = LocalNeuralBackend()
            b._model = MagicMock()
            b._tokenizer = MagicMock()
            h = b.health()
            assert h["ok"] is True
            assert h["loaded"] is True

    def test_health_unloaded_by_ram(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", True):
            b = LocalNeuralBackend()
            b._unloaded_by_ram = True
            h = b.health()
            assert h["ok"] is False

    def test_health_with_load_error(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", True):
            b = LocalNeuralBackend()
            b._load_error = "some error"
            h = b.health()
            assert h["ok"] is False


class TestLocalNeuralBackendUnload:
    def test_unload_clears_model(self):
        b = LocalNeuralBackend()
        b._model = MagicMock()
        b._tokenizer = MagicMock()
        with patch.object(lb_mod, "_torch", MagicMock()):
            b.unload()
        assert b._model is None
        assert b._tokenizer is None

    def test_unload_when_already_none(self):
        b = LocalNeuralBackend()
        b._model = None
        b._tokenizer = None
        b.unload()  # no error

    def test_unload_with_monitoring(self):
        mon = MagicMock()
        b = LocalNeuralBackend(monitoring=mon)
        b._model = MagicMock()
        b._tokenizer = MagicMock()
        with patch.object(lb_mod, "_torch", MagicMock()):
            b.unload()
        mon.info.assert_called()


class TestLocalNeuralBackendSetModel:
    def test_set_model(self):
        b = LocalNeuralBackend(model="old/model")
        b._model = MagicMock()
        b._tokenizer = MagicMock()
        b._load_error = "old error"
        b.set_model("new/model")
        assert b.model == "new/model"
        assert b._model is None
        assert b._tokenizer is None
        assert b._load_error == ""

    def test_set_model_none_keeps_old(self):
        b = LocalNeuralBackend(model="orig")
        b.set_model(None)
        assert b.model == "orig"


class TestLocalNeuralBackendProperties:
    def test_total_tokens(self):
        b = LocalNeuralBackend()
        assert b.total_tokens == 0
        b._total_tokens = 42
        assert b.total_tokens == 42

    def test_total_cost_usd(self):
        b = LocalNeuralBackend()
        assert b.total_cost_usd == 0.0


class TestLocalNeuralBackendInfer:
    def _make_backend_with_mocks(self):
        """Create a backend with mocked model/tokenizer."""
        b = LocalNeuralBackend()

        tokenizer = MagicMock()
        tokenizer.eos_token_id = 0
        tokenizer.pad_token = None
        tokenizer.pad_token_id = None

        # Simulate tokenizer call
        import types
        fake_input_ids = MagicMock()
        fake_input_ids.shape = MagicMock()
        fake_input_ids.shape.__getitem__ = lambda self, i: 10  # 10 input tokens
        tokenizer.return_value = {"input_ids": fake_input_ids}
        tokenizer.decode.return_value = "generated text"
        tokenizer.apply_chat_template.return_value = "formatted prompt"

        model = MagicMock()
        # model.generate returns tensor-like output
        output_ids_inner = MagicMock()
        output_ids_inner.shape = MagicMock()
        output_ids_inner.shape.__getitem__ = lambda self, i: 5  # 5 generated tokens
        output_ids = MagicMock()
        output_ids.__getitem__ = lambda self, i: output_ids_inner
        model.generate.return_value = output_ids

        b._model = model
        b._tokenizer = tokenizer
        b._device = "cpu"
        return b

    def test_infer_empty_prompt(self):
        b = LocalNeuralBackend()
        assert b.infer("") == ""
        assert b.infer(None) == ""

    def test_infer_no_model_raises(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", False):
            b = LocalNeuralBackend()
            with pytest.raises(RuntimeError):
                b.infer("hello")

    def test_infer_basic(self):
        b = self._make_backend_with_mocks()
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(lb_mod, "_torch", mock_torch), \
             patch.dict(os.environ, {"LOCAL_LLM_AUTO_UNLOAD": "false"}):
            result = b.infer("test prompt")
            assert isinstance(result, str)

    def test_infer_with_system(self):
        b = self._make_backend_with_mocks()
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(lb_mod, "_torch", mock_torch), \
             patch.dict(os.environ, {"LOCAL_LLM_AUTO_UNLOAD": "false"}):
            result = b.infer("question", system="You are helpful")
            assert isinstance(result, str)

    def test_infer_with_context(self):
        b = self._make_backend_with_mocks()
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(lb_mod, "_torch", mock_torch), \
             patch.dict(os.environ, {"LOCAL_LLM_AUTO_UNLOAD": "false"}):
            result = b.infer("q", context={"key": "val"})
            assert isinstance(result, str)

    def test_infer_with_history(self):
        b = self._make_backend_with_mocks()
        mock_torch = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock(return_value=False)

        with patch.object(lb_mod, "_torch", mock_torch), \
             patch.dict(os.environ, {"LOCAL_LLM_AUTO_UNLOAD": "false"}):
            result = b.infer("q", history=[
                {"role": "user", "content": "prev"},
                {"role": "assistant", "content": "ans"},
            ])
            assert isinstance(result, str)


class TestLocalNeuralBackendToPrompt:
    def test_to_prompt_no_tokenizer(self):
        b = LocalNeuralBackend()
        b._tokenizer = None
        messages = [{"role": "user", "content": "hello"}]
        prompt = b._to_prompt(messages)
        assert "user" in prompt
        assert "hello" in prompt

    def test_to_prompt_with_chat_template(self):
        b = LocalNeuralBackend()
        tokenizer = MagicMock()
        tokenizer.apply_chat_template.return_value = "formatted"
        b._tokenizer = tokenizer
        result = b._to_prompt([{"role": "user", "content": "hi"}])
        assert result == "formatted"

    def test_to_prompt_chat_template_fails(self):
        b = LocalNeuralBackend()
        tokenizer = MagicMock()
        tokenizer.apply_chat_template.side_effect = TypeError("no template")
        b._tokenizer = tokenizer
        result = b._to_prompt([{"role": "user", "content": "hi"}])
        # Falls back to simple format
        assert "hi" in result


class TestLocalNeuralBackendEnsureLoaded:
    def test_ensure_loaded_already_loaded(self):
        b = LocalNeuralBackend()
        b._model = MagicMock()
        b._tokenizer = MagicMock()
        b._ensure_loaded()  # no error, no re-load

    def test_ensure_loaded_no_transformers(self):
        with patch.object(lb_mod, "_TRANSFORMERS_AVAILABLE", False):
            b = LocalNeuralBackend()
            with pytest.raises(RuntimeError, match="requires"):
                b._ensure_loaded()
