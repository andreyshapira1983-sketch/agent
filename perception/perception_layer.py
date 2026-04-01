# Perception Layer (восприятие среды) — Слой 1
# Архитектура автономного AI-агента
# Задача: получать информацию из мира и передавать её в Knowledge System и Cognitive Core.
# pylint: disable=broad-except

from perception.text_classifier import TextClassifier

try:
    from safety.content_fence import sanitize_external, detect_injection
except ImportError:
    def sanitize_external(text, source='', **_kw):  # type: ignore[misc]
        return text, []
    def detect_injection(text):   # type: ignore[misc]
        return []


class PerceptionLayer:
    """
    Слой восприятия среды (Слой 1).

    Получает информацию из всех источников: интернет, файлы, API, документы,
    изображения, аудио, GitHub, пользовательские сообщения.

    Подсистемы:
        - web_crawler       — обход веб-страниц
        - document_parser   — разбор документов (PDF, TXT, DOCX и др.)
        - api_client        — запросы к внешним API
        - file_reader       — чтение локальных файлов
        - image_recognizer  — распознавание изображений
        - speech_recognizer — распознавание речи/аудио
        - text_classifier   — классификация типа текста (CODE/SCIENCE/LITERATURE/NEWS/...)

    Передаёт данные в:
        - Knowledge System (Слой 2)
        - Cognitive Core (Слой 3) через build_context()
    """

    def __init__(
        self,
        web_crawler=None,
        document_parser=None,
        api_client=None,
        file_reader=None,
        image_recognizer=None,
        speech_recognizer=None,
        speech_synthesizer=None,
        text_classifier=None,
    ):
        self.web_crawler = web_crawler
        self.document_parser = document_parser
        self.api_client = api_client
        self.file_reader = file_reader
        self.image_recognizer = image_recognizer
        self.speech_recognizer = speech_recognizer
        self.speech_synthesizer = speech_synthesizer
        # TextClassifier — детерминированный, без LLM; создаём сами если не передан
        self.text_classifier: TextClassifier = text_classifier or TextClassifier()

        self._last_data = {}

    # ── Классификация текста ──────────────────────────────────────────────────

    def classify_text(self, text: str, source_hint: str = ''):
        """
        Классифицирует тип текста. Возвращает TextClassification.
        Вызывается автоматически внутри fetch_web, parse_document, read_file,
        receive_message — результат хранится в _last_data['text_type'].
        """
        return self.text_classifier.classify(text, source_hint=source_hint)

    def _attach_text_type(self, _key: str, text: str, source_hint: str = ''):
        """Классифицирует text и прикрепляет TextClassification к _last_data."""
        try:
            classification = self.text_classifier.classify(text, source_hint=source_hint)
            self._last_data['text_type'] = classification.to_dict()
            self._last_data['text_type_raw'] = classification
        except Exception:
            pass  # классификатор не должен ломать восприятие

    def _fence_external(self, text: str, source: str) -> str:
        """Проверяет внешний текст на injection и оборачивает в fence-маркеры."""
        fenced, hits = sanitize_external(text, source=source, max_len=7000)
        if hits:
            self._last_data.setdefault('injection_alerts', []).append({
                'source': source, 'patterns': hits,
            })
        return fenced

    # ── Основной интерфейс (используется Cognitive Core) ──────────────────────

    def get_data(self):
        """Возвращает последний собранный срез данных восприятия."""
        return self._last_data

    # ── Web ───────────────────────────────────────────────────────────────────

    def fetch_web(self, url):
        """Получает содержимое веб-страницы через web crawler."""
        if not self.web_crawler:
            raise NotImplementedError("web_crawler не подключён")
        result = self.web_crawler.fetch(url)
        # Защита от indirect prompt injection: fence внешний текст
        if isinstance(result, dict) and result.get('text'):
            result['text_raw'] = result['text']
            result['text'] = self._fence_external(result['text'], source=url)
        elif isinstance(result, str):
            result = self._fence_external(result, source=url)
        self._last_data['web'] = result
        if isinstance(result, str):
            self._attach_text_type('web', result, source_hint=url)
        elif isinstance(result, dict):
            self._attach_text_type('web', result.get('text_raw', ''), source_hint=url)
        return result

    def crawl(self, start_url, depth=1):
        """Обходит сайт начиная с start_url на глубину depth."""
        if not self.web_crawler:
            raise NotImplementedError("web_crawler не подключён")
        result = self.web_crawler.crawl(start_url, depth=depth)
        # Fence каждую страницу
        if isinstance(result, list):
            for page in result:
                if isinstance(page, dict) and page.get('text'):
                    page['text_raw'] = page['text']
                    page['text'] = self._fence_external(
                        page['text'], source=page.get('url', start_url))
        self._last_data['crawl'] = result
        return result

    # ── Documents ─────────────────────────────────────────────────────────────

    def parse_document(self, source):
        """Разбирает документ (PDF, DOCX, TXT, HTML и др.) и извлекает текст и метаданные."""
        if not self.document_parser:
            raise NotImplementedError("document_parser не подключён")
        result = self.document_parser.parse(source)
        # Извлекаем текст для fence-обработки
        if isinstance(result, dict):
            text = str(result.get('text', '') or '')
        elif hasattr(result, 'text'):
            text = str(getattr(result, 'text', '') or '')
        else:
            text = str(result or '')
        # Защита от indirect prompt injection в документах
        if text:
            fenced = self._fence_external(text, source=str(source))
            if isinstance(result, dict):
                result['text_raw'] = text
                result['text'] = fenced
            elif hasattr(result, 'text'):
                result.text_raw = text
                result.text = fenced
            self._attach_text_type('document', text, source_hint=str(source))
        self._last_data['document'] = result
        return result

    # ── API ───────────────────────────────────────────────────────────────────

    def call_api(self, endpoint, method='GET', params=None, headers=None, body=None):
        """Делает запрос к внешнему API сервису."""
        if not self.api_client:
            raise NotImplementedError("api_client не подключён")
        result = self.api_client.request(
            endpoint, method=method, params=params, headers=headers, body=body
        )
        self._last_data['api'] = result
        return result

    # ── Files ─────────────────────────────────────────────────────────────────

    def read_file(self, path):
        """Читает локальный файл (текст, данные, изображение, видео, аудио)."""
        if not self.file_reader:
            raise NotImplementedError("file_reader не подключён")
        result = self.file_reader.read(path)
        self._last_data['file'] = result
        text = str(result) if result else ''
        if text:
            self._attach_text_type('file', text, source_hint=str(path))
        return result

    # ── Images ────────────────────────────────────────────────────────────────

    def recognize_image(self, source):
        """Распознаёт содержимое изображения (путь к файлу или URL)."""
        if not self.image_recognizer:
            raise NotImplementedError("image_recognizer не подключён")
        result = self.image_recognizer.analyze(source)
        self._last_data['image'] = result
        return result

    # ── Audio / Speech ────────────────────────────────────────────────────────

    def transcribe_speech(self, source):
        """Транскрибирует аудиофайл или поток в текст."""
        if not self.speech_recognizer:
            raise NotImplementedError("speech_recognizer не подключён")
        result = self.speech_recognizer.transcribe(source)
        self._last_data['speech'] = result
        return result

    # ── User input ────────────────────────────────────────────────────────────

    def receive_message(self, message, sender=None):
        """Принимает сообщение от пользователя или внешней системы."""
        entry = {'text': message, 'sender': sender}
        self._last_data['message'] = entry
        if message:
            self._attach_text_type('message', str(message))
        return entry

    # ── Сводный срез ─────────────────────────────────────────────────────────

    def collect(self, sources: dict):
        """
        Собирает данные из нескольких источников за один вызов.

        sources — словарь вида:
            {
                'web': 'https://...',
                'file': '/path/to/file',
                'message': 'текст от пользователя',
                ...
            }
        Возвращает объединённый срез данных.
        """
        for source_type, value in sources.items():
            if source_type == 'web':
                self.fetch_web(value)
            elif source_type == 'file':
                self.read_file(value)
            elif source_type == 'document':
                self.parse_document(value)
            elif source_type == 'api':
                # Pre-fetch API result if needed (checked but not assigned yet)
                if isinstance(value, dict):
                    self.call_api(**value)
                else:
                    self.call_api(value)
            elif source_type == 'image':
                self.recognize_image(value)
            elif source_type == 'speech':
                self.transcribe_speech(value)
            elif source_type == 'message':
                self.receive_message(value)
        return self._last_data
