# HuggingFace Hub Backend — поиск моделей и датасетов через HF API
# Подключается к KnowledgeAcquisitionPipeline(huggingface_backend=HuggingFaceBackend(token=...))

import importlib

try:
    _requests_module = importlib.import_module('requests')
    _REQUESTS_AVAILABLE = True
except ImportError:
    _requests_module = None  # type: ignore[assignment]
    _REQUESTS_AVAILABLE = False


class HuggingFaceBackend:
    """
    Клиент HuggingFace Hub REST API на основе requests.

    Реализует методы для поиска моделей и датасетов:
        search_models(query, limit=10, sort='downloads')
        search_datasets(query, limit=10, sort='downloads')
        get_model_info(model_id)
        get_dataset_info(dataset_id)
    """

    BASE = "https://huggingface.co/api"

    def __init__(self, token: str | None = None):
        if not _REQUESTS_AVAILABLE or _requests_module is None:
            raise ImportError("Установи requests: pip install requests")
        self._requests = _requests_module
        self._RequestException = _requests_module.exceptions.RequestException

        self._token = token
        self._headers = {
            "User-Agent": "autonomous-ai-agent",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    # ── Поиск моделей ─────────────────────────────────────────────────────────

    def search_models(self, query: str, limit: int = 10, sort: str = 'downloads') -> list:
        """
        Поиск моделей на HuggingFace Hub.

        Args:
            query  — поисковый запрос (например, 'bert', 'text-generation')
            limit  — максимальное число результатов
            sort   — сортировка: 'downloads', 'trending', 'likes', 'updated'

        Returns:
            Список словарей с информацией о модели.
        """
        url = f"{self.BASE}/models"
        params = {
            'search': query,
            'sort': sort,
            'limit': limit,
        }
        try:
            resp = self._requests.get(url, params=params, headers=self._headers, timeout=10)
            resp.raise_for_status()
            models = resp.json()
            if not isinstance(models, list):
                return []
            return [
                {
                    'id': m.get('id', ''),
                    'name': m.get('id', ''),  # model_id
                    'downloads': m.get('downloads', 0),
                    'likes': m.get('likes', 0),
                    'library_name': m.get('library_name', 'pytorch'),
                    'tasks': m.get('tasks', []),
                    'modelId': m.get('id', ''),
                }
                for m in models
            ]
        except (self._RequestException, ValueError, TypeError) as e:
            return [{'error': str(e)}]

    def search_datasets(self, query: str, limit: int = 10, sort: str = 'downloads') -> list:
        """
        Поиск датасетов на HuggingFace Hub.

        Args:
            query  — поисковый запрос
            limit  — максимальное число результатов
            sort   — сортировка

        Returns:
            Список словарей с информацией о датасете.
        """
        url = f"{self.BASE}/datasets"
        params = {
            'search': query,
            'sort': sort,
            'limit': limit,
        }
        try:
            resp = self._requests.get(url, params=params, headers=self._headers, timeout=10)
            resp.raise_for_status()
            datasets = resp.json()
            if not isinstance(datasets, list):
                return []
            return [
                {
                    'id': d.get('id', ''),
                    'name': d.get('id', ''),  # dataset_id
                    'downloads': d.get('downloads', 0),
                    'likes': d.get('likes', 0),
                    'description': d.get('description', ''),
                }
                for d in datasets
            ]
        except (self._RequestException, ValueError, TypeError) as e:
            return [{'error': str(e)}]

    def get_model_info(self, model_id: str) -> dict:
        """
        Получает информацию о конкретной модели.

        Args:
            model_id — ID модели (например, 'bert-base-multilingual-cased')

        Returns:
            Словарь с описанием модели.
        """
        url = f"{self.BASE}/models/{model_id}"
        try:
            resp = self._requests.get(url, headers=self._headers, timeout=10)
            resp.raise_for_status()
            m = resp.json()
            return {
                'id': m.get('id', ''),
                'name': m.get('id', ''),
                'description': m.get('description', ''),
                'downloads': m.get('downloads', 0),
                'likes': m.get('likes', 0),
                'library_name': m.get('library_name', ''),
                'tags': m.get('tags', []),
                'last_modified': m.get('lastModified', ''),
            }
        except (self._RequestException, ValueError, TypeError) as e:
            return {'error': str(e), 'model_id': model_id}

    def get_dataset_info(self, dataset_id: str) -> dict:
        """
        Получает информацию о конкретном датасете.

        Args:
            dataset_id — ID датасета

        Returns:
            Словарь с описанием датасета.
        """
        url = f"{self.BASE}/datasets/{dataset_id}"
        try:
            resp = self._requests.get(url, headers=self._headers, timeout=10)
            resp.raise_for_status()
            d = resp.json()
            return {
                'id': d.get('id', ''),
                'name': d.get('id', ''),
                'description': d.get('description', ''),
                'downloads': d.get('downloads', 0),
                'likes': d.get('likes', 0),
            }
        except (self._RequestException, ValueError, TypeError) as e:
            return {'error': str(e), 'dataset_id': dataset_id}
