# Image Recognizer — подсистема Perception Layer (Слой 1)
# Архитектура автономного AI-агента
# Распознавание изображений через OpenAI Vision (gpt-4o).

from __future__ import annotations

import base64
import os
import time
from typing import Any, cast


class ImageRecognitionResult:
    """Результат распознавания изображения."""

    def __init__(self, path: str, description: str, objects: list[str] | None = None,
                 text_in_image: str | None = None, confidence: float = 1.0):
        self.path = path
        self.description = description
        self.objects = objects or []
        self.text_in_image = text_in_image or ''
        self.confidence = confidence
        self.recognized_at = time.time()

    def to_dict(self) -> dict:
        return {
            'path': self.path,
            'description': self.description[:500],
            'objects': self.objects,
            'text_in_image': self.text_in_image[:300],
            'confidence': self.confidence,
        }


class ImageRecognizer:
    """
    Image Recognizer — подсистема PerceptionLayer.

    Возможности:
        - describe(path)    — подробное описание изображения
        - ocr(path)         — извлечение текста с изображения (OCR)
        - detect(path)      — список объектов на изображении
        - analyze(path)     — полный анализ: описание + объекты + текст

    Использует OpenAI Vision (gpt-4o / gpt-4o-mini).
    Поддерживает: JPEG, PNG, GIF, WEBP.
    Работает с локальными файлами (base64) и URL.

    Fallback без API: возвращает заглушку с предупреждением.
    """

    SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    MAX_IMAGE_MB = 20

    def __init__(self, openai_client=None, model: str = 'gpt-4o',
                 monitoring=None):
        """
        Args:
            openai_client — экземпляр OpenAIClient (из llm/openai_client.py)
            model         — 'gpt-4o' или 'gpt-4o-mini' (оба поддерживают vision)
        """
        self.client = openai_client
        self.model = model
        self.monitoring = monitoring

    # ── Основные методы ───────────────────────────────────────────────────────

    def describe(self, path_or_url: str, detail: str = 'auto') -> str:
        """
        Возвращает подробное описание изображения.

        Args:
            path_or_url — путь к файлу или http(s)-URL
            detail      — 'low' / 'high' / 'auto'
        """
        result = self.analyze(path_or_url, detail=detail)
        return result.description

    def ocr(self, path_or_url: str) -> str:
        """Извлекает весь текст с изображения (OCR)."""
        if not self.client:
            return ''
        image_content = self._load(path_or_url)
        if not image_content:
            return ''
        raw = self._vision_call(
            image_content,
            prompt=(
                'Извлеки весь текст, видимый на изображении. '
                'Сохрани оригинальную структуру (абзацы, списки). '
                'Верни только текст, без пояснений.'
            ),
            detail='high',
        )
        return raw.strip()

    def detect_objects(self, path_or_url: str) -> list[str]:
        """Возвращает список объектов, обнаруженных на изображении."""
        if not self.client:
            return []
        image_content = self._load(path_or_url)
        if not image_content:
            return []
        raw = self._vision_call(
            image_content,
            prompt=(
                'Перечисли все объекты, которые видишь на изображении. '
                'Один объект — одна строка. Без нумерации и пояснений.'
            ),
        )
        return [line.strip() for line in raw.splitlines() if line.strip()]

    def analyze(self, path_or_url: str, detail: str = 'auto') -> ImageRecognitionResult:
        """
        Полный анализ: описание + объекты + OCR.

        Returns:
            ImageRecognitionResult
        """
        name = path_or_url if path_or_url.startswith('http') else os.path.basename(path_or_url)

        if not self.client:
            self._log('image_recognizer: openai_client не подключён', level='warning')
            return ImageRecognitionResult(
                path=path_or_url,
                description='[Image recognition недоступен: OpenAI client не подключён]',
                confidence=0.0,
            )

        image_content = self._load(path_or_url)
        if not image_content:
            return ImageRecognitionResult(
                path=path_or_url,
                description='[Не удалось загрузить изображение]',
                confidence=0.0,
            )

        # Один запрос для всего анализа
        raw = self._vision_call(
            image_content,
            prompt=(
                'Проанализируй изображение и предоставь:\n'
                '1. ОПИСАНИЕ: подробное описание (2-4 предложения)\n'
                '2. ОБЪЕКТЫ: список всех объектов (через запятую)\n'
                '3. ТЕКСТ: весь текст на изображении (если есть, иначе "нет")\n\n'
                'Формат ответа строго такой:\n'
                'ОПИСАНИЕ: ...\n'
                'ОБЪЕКТЫ: ...\n'
                'ТЕКСТ: ...'
            ),
            detail=detail,
        )

        description, objects, text_in_image = self._parse_analysis(raw)

        result = ImageRecognitionResult(
            path=path_or_url,
            description=description,
            objects=[o.strip() for o in objects.split(',') if o.strip()],
            text_in_image=text_in_image if text_in_image.lower() != 'нет' else '',
            confidence=0.9,
        )
        self._log(f"Изображение '{name}': {len(result.objects)} объектов")
        return result

    def analyze_url(self, url: str) -> ImageRecognitionResult:
        """Анализирует изображение по URL (без скачивания)."""
        return self.analyze(url)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load(self, path_or_url: str) -> dict | None:
        """Возвращает image_content для OpenAI API."""
        if path_or_url.startswith(('http://', 'https://')):
            return {'type': 'image_url', 'image_url': {'url': path_or_url}}

        if not os.path.exists(path_or_url):
            self._log(f'Файл не найден: {path_or_url}', level='error')
            return None

        ext = os.path.splitext(path_or_url)[1].lower()
        if ext not in self.SUPPORTED_FORMATS:
            self._log(f'Неподдерживаемый формат: {ext}', level='warning')
            return None

        size_mb = os.path.getsize(path_or_url) / (1024 * 1024)
        if size_mb > self.MAX_IMAGE_MB:
            self._log(f'Файл слишком большой: {size_mb:.1f}MB', level='warning')
            return None

        mime_map = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png', '.gif': 'image/gif',
            '.webp': 'image/webp', '.bmp': 'image/bmp',
        }
        mime = mime_map.get(ext, 'image/jpeg')

        with open(path_or_url, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')

        return {
            'type': 'image_url',
            'image_url': {'url': f'data:{mime};base64,{b64}'},
        }

    def _vision_call(self, image_content: dict, prompt: str,
                     detail: str = 'auto') -> str:
        """Вызывает OpenAI Vision API."""
        try:
            # Встраиваем detail в image_url если передан
            img = dict(image_content)
            if img.get('type') == 'image_url' and detail != 'auto':
                img['image_url'] = dict(img.get('image_url', {}))
                img['image_url']['detail'] = detail

            # Строим сообщение вручную — openai_client.infer() работает только с текстом,
            # поэтому вызываем нативный клиент напрямую
            import openai as _openai
            # SECURITY: не читаем os.environ напрямую — только через уже инициализированный клиент
            _inner = getattr(self.client, '_client', None)
            if _inner is not None:
                oa = _inner
            elif self.client is not None and hasattr(self.client, 'api_key'):
                oa = _openai.OpenAI(api_key=self.client.api_key)
            else:
                self._log('Vision API: клиент OpenAI не инициализирован', level='error')
                return ''
            response = oa.chat.completions.create(
                model=self.model,
                messages=cast(Any, [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        img,
                    ],
                }]),
                max_tokens=1024,
            )
            return response.choices[0].message.content or ''
        except Exception as e:  # pylint: disable=broad-except
            self._log(f'Vision API error: {e}', level='error')
            return ''

    @staticmethod
    def _parse_analysis(raw: str) -> tuple[str, str, str]:
        """Парсит структурированный ответ анализа."""
        import re
        description = ''
        objects = ''
        text_in_image = ''

        m = re.search(r'ОПИСАНИЕ:\s*(.+?)(?=ОБЪЕКТЫ:|ТЕКСТ:|$)', raw, re.DOTALL | re.IGNORECASE)
        if m:
            description = m.group(1).strip()

        m = re.search(r'ОБЪЕКТЫ:\s*(.+?)(?=ОПИСАНИЕ:|ТЕКСТ:|$)', raw, re.DOTALL | re.IGNORECASE)
        if m:
            objects = m.group(1).strip()

        m = re.search(r'ТЕКСТ:\s*(.+?)(?=ОПИСАНИЕ:|ОБЪЕКТЫ:|$)', raw, re.DOTALL | re.IGNORECASE)
        if m:
            text_in_image = m.group(1).strip()

        if not description:
            description = raw.strip()[:500]

        return description, objects, text_in_image

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='image_recognizer'
            )
        else:
            print(f'[ImageRecognizer] {message}')
