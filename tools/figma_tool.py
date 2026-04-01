"""
FigmaTool — работа с Figma через REST API.

Бесплатно: личный токен (Personal Access Token) на figma.com/settings
Что умеет:
  - read_file    → структура файла (фреймы, компоненты, цвета)
  - export_image → экспорт ноды в PNG / SVG / PDF / JPG
  - list_pages   → список страниц в файле
  - get_comments → комментарии к файлу
  - add_comment  → добавить комментарий (позиция x/y или без)
  - get_styles   → стили (цвета, шрифты, ефекты)
  - get_components → компоненты библиотеки

ENV: FIGMA_ACCESS_TOKEN=<personal access token>
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tools.tool_layer import BaseTool


_FIGMA_API = 'https://api.figma.com/v1'


class FigmaTool(BaseTool):
    """Figma REST API — читает файлы, экспортирует картинки, добавляет комментарии."""

    def __init__(self, access_token: str | None = None):
        super().__init__(
            'figma',
            'Figma API: читать файлы дизайна, экспортировать PNG/SVG, добавлять комментарии'
        )
        self._token = access_token or os.environ.get('FIGMA_ACCESS_TOKEN', '')

    # ── внутренний запрос ────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self._token:
            return {'error': 'FIGMA_ACCESS_TOKEN не задан — получи токен на figma.com/settings'}
        url = f'{_FIGMA_API}{path}'
        headers = {
            'X-Figma-Token': self._token,
            'Content-Type': 'application/json',
        }
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {'error': f'HTTP {e.code}: {e.read().decode()[:300]}'}
        except Exception as e:
            return {'error': str(e)}

    # ── действия ────────────────────────────────────────────────────────────

    def get_me(self) -> dict:
        """Информация о текущем пользователе Figma (проверка токена)."""
        r = self._request('GET', '/me')
        if 'error' in r:
            return r
        return {
            'id': r.get('id', ''),
            'email': r.get('email', ''),
            'handle': r.get('handle', ''),
            'success': True,
        }

    def read_file(self, file_key: str) -> dict:
        """Полная структура Figma-файла (страницы, ноды, компоненты)."""
        r = self._request('GET', f'/files/{file_key}')
        if 'error' in r:
            return r
        doc = r.get('document', {})
        pages = [{'id': c['id'], 'name': c['name']} for c in doc.get('children', [])]
        return {
            'name': r.get('name', ''),
            'last_modified': r.get('lastModified', ''),
            'pages': pages,
            'components': list(r.get('components', {}).keys()),
            'styles': list(r.get('styles', {}).keys()),
        }

    def list_pages(self, file_key: str) -> dict:
        """Список страниц файла Figma."""
        r = self._request('GET', f'/files/{file_key}')
        if 'error' in r:
            return r
        doc = r.get('document', {})
        pages = [
            {'id': c['id'], 'name': c['name'], 'nodes': len(c.get('children', []))}
            for c in doc.get('children', [])
        ]
        return {'file_key': file_key, 'pages': pages}

    def export_image(
        self,
        file_key: str,
        node_ids: str | list[str],
        fmt: str = 'png',
        scale: float = 1.0,
        output_dir: str = 'outputs',
    ) -> dict:
        """
        Экспорт нод в изображения.
        fmt: png | jpg | svg | pdf
        node_ids: один ID ноды или список.
        """
        if isinstance(node_ids, str):
            node_ids = [node_ids]
        ids_str = ','.join(node_ids)
        path = f'/images/{file_key}?ids={urllib.parse.quote(ids_str)}&format={fmt}&scale={scale}'
        r = self._request('GET', path)
        if 'error' in r:
            return r

        images: dict[str, str] = r.get('images', {})
        saved: list[dict] = []
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        for node_id, img_url in images.items():
            if not img_url:
                saved.append({'node_id': node_id, 'error': 'пустой URL от Figma'})
                continue
            fname = out_dir / f'figma_{node_id.replace(":", "_")}.{fmt}'
            try:
                urllib.request.urlretrieve(img_url, fname)
                saved.append({'node_id': node_id, 'file': str(fname)})
            except Exception as e:
                saved.append({'node_id': node_id, 'error': str(e)})

        return {'exported': saved, 'format': fmt}

    def get_comments(self, file_key: str) -> dict:
        """Все комментарии к файлу."""
        r = self._request('GET', f'/files/{file_key}/comments')
        if 'error' in r:
            return r
        comments = [
            {
                'id': c.get('id'),
                'message': c.get('message', ''),
                'author': c.get('user', {}).get('handle', ''),
                'created': c.get('created_at', ''),
            }
            for c in r.get('comments', [])
        ]
        return {'file_key': file_key, 'count': len(comments), 'comments': comments}

    def add_comment(
        self,
        file_key: str,
        message: str,
        x: float | None = None,
        y: float | None = None,
    ) -> dict:
        """Добавить комментарий к файлу. x/y — координаты на холсте (необязательно)."""
        body: dict = {'message': message}
        if x is not None and y is not None:
            body['client_meta'] = {'x': x, 'y': y}
        r = self._request('POST', f'/files/{file_key}/comments', body)
        if 'error' in r:
            return r
        return {'id': r.get('id'), 'message': message, 'ok': True}

    def get_styles(self, file_key: str) -> dict:
        """Стили файла (цвета, шрифты, эффекты)."""
        r = self._request('GET', f'/files/{file_key}/styles')
        if 'error' in r:
            return r
        styles = [
            {
                'key': s.get('key'),
                'name': s.get('name', ''),
                'style_type': s.get('style_type', ''),
                'description': s.get('description', ''),
            }
            for s in r.get('meta', {}).get('styles', [])
        ]
        return {'file_key': file_key, 'count': len(styles), 'styles': styles}

    def get_components(self, file_key: str) -> dict:
        """Компоненты файла (переиспользуемые блоки)."""
        r = self._request('GET', f'/files/{file_key}/components')
        if 'error' in r:
            return r
        comps = [
            {
                'key': c.get('key'),
                'name': c.get('name', ''),
                'description': c.get('description', ''),
                'node_id': c.get('node_id', ''),
            }
            for c in r.get('meta', {}).get('components', [])
        ]
        return {'file_key': file_key, 'count': len(comps), 'components': comps}

    # ── универсальный dispatch ───────────────────────────────────────────────

    def run(self, action: str = 'read_file', **params) -> dict:
        """
        action: read_file | list_pages | export_image | get_comments |
                add_comment | get_styles | get_components
        """
        dispatch = {
            'get_me':         self.get_me,
            'read_file':      self.read_file,
            'list_pages':     self.list_pages,
            'export_image':   self.export_image,
            'get_comments':   self.get_comments,
            'add_comment':    self.add_comment,
            'get_styles':     self.get_styles,
            'get_components': self.get_components,
        }
        fn = dispatch.get(action)
        if not fn:
            return {'error': f'Неизвестный action: {action}. Доступны: {list(dispatch)}'}
        try:
            return fn(**params)
        except TypeError as e:
            return {'error': f'Неверные параметры для {action}: {e}'}
