"""Tests for llm/huggingface_backend.py — HuggingFaceBackend."""
from unittest.mock import patch, MagicMock
import pytest


class TestHuggingFaceBackend:
    def _make_backend(self, token=None):
        mock_requests = MagicMock()
        mock_requests.exceptions.RequestException = Exception
        with patch.dict('sys.modules', {'requests': mock_requests}):
            # Re-import to pick up mock
            import importlib
            import llm.huggingface_backend as hf_mod
            importlib.reload(hf_mod)
            hf_mod._REQUESTS_AVAILABLE = True
            hf_mod._requests_module = mock_requests
            backend = hf_mod.HuggingFaceBackend(token=token)
        backend._mock_requests = mock_requests
        return backend

    def test_init_no_token(self):
        b = self._make_backend()
        assert b._token is None
        assert 'Authorization' not in b._headers

    def test_init_with_token(self):
        b = self._make_backend(token='hf_test123')
        assert b._token == 'hf_test123'
        assert 'Authorization' in b._headers
        assert b._headers['Authorization'] == 'Bearer hf_test123'

    def test_init_no_requests(self):
        with patch.dict('sys.modules', {'requests': None}):
            import importlib
            import llm.huggingface_backend as hf_mod
            importlib.reload(hf_mod)
            hf_mod._REQUESTS_AVAILABLE = False
            hf_mod._requests_module = None
            with pytest.raises(ImportError):
                hf_mod.HuggingFaceBackend()

    def test_search_models(self):
        b = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {'id': 'bert-base', 'downloads': 1000, 'likes': 50, 'pipeline_tag': 'fill-mask'},
        ]
        mock_resp.raise_for_status = MagicMock()
        b._mock_requests.get.return_value = mock_resp

        result = b.search_models('bert', limit=5)
        assert len(result) >= 1
        assert result[0]['id'] == 'bert-base'

    def test_search_models_not_list(self):
        b = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'error': 'bad'}
        mock_resp.raise_for_status = MagicMock()
        b._mock_requests.get.return_value = mock_resp

        result = b.search_models('bert')
        assert result == []

    def test_search_models_request_error(self):
        b = self._make_backend()
        b._mock_requests.get.side_effect = b._RequestException("timeout")

        result = b.search_models('bert')
        # On error, returns list with error dict or empty
        assert isinstance(result, list)

    def test_search_datasets(self):
        b = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {'id': 'squad', 'downloads': 500, 'likes': 30},
        ]
        mock_resp.raise_for_status = MagicMock()
        b._mock_requests.get.return_value = mock_resp

        result = b.search_datasets('qa', limit=3)
        assert len(result) >= 1

    def test_get_model_info(self):
        b = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            'id': 'bert-base',
            'pipeline_tag': 'fill-mask',
            'downloads': 1000,
        }
        mock_resp.raise_for_status = MagicMock()
        b._mock_requests.get.return_value = mock_resp

        result = b.get_model_info('bert-base')
        assert result.get('id') == 'bert-base'

    def test_get_model_info_error(self):
        b = self._make_backend()
        b._mock_requests.get.side_effect = b._RequestException("not found")

        result = b.get_model_info('nonexistent')
        assert result == {} or result.get('error')

    def test_get_dataset_info(self):
        b = self._make_backend()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {'id': 'squad', 'downloads': 500}
        mock_resp.raise_for_status = MagicMock()
        b._mock_requests.get.return_value = mock_resp

        result = b.get_dataset_info('squad')
        assert result.get('id') == 'squad'
