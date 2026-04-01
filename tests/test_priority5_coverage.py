"""
Priority-5 coverage: perception/* (text_classifier, document_parser, perception_layer)
and knowledge/source_backends.py (public API structure tests).
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ═══════════════════════════════════════════════════════════════════════════
# 1. perception/text_classifier.py
# ═══════════════════════════════════════════════════════════════════════════
from perception.text_classifier import TextClassifier, TextClassification, TextType


class TestTextType(unittest.TestCase):
    def test_enum_values(self):
        for name in ('CODE', 'SCIENCE', 'LITERATURE', 'NEWS', 'DOCUMENT', 'WEB', 'CONVERSATION'):
            self.assertTrue(hasattr(TextType, name))


class TestTextClassification(unittest.TestCase):
    def test_create(self):
        tc = TextClassification(TextType.CODE, 0.9, language='python')
        self.assertEqual(tc.text_type, TextType.CODE)
        self.assertEqual(tc.confidence, 0.9)

    def test_to_dict(self):
        tc = TextClassification(TextType.NEWS, 0.7, language='en', signals=['headline'])
        d = tc.to_dict()
        self.assertIn('text_type', d)
        self.assertIn('confidence', d)


class TestTextClassifier(unittest.TestCase):
    def setUp(self):
        self.tc = TextClassifier()

    def test_classify_code(self):
        code = 'def hello():\n    print("hello world")\n\nif __name__ == "__main__":\n    hello()\n'
        r = self.tc.classify(code)
        self.assertIsInstance(r, TextClassification)
        self.assertEqual(r.text_type, TextType.CODE)

    def test_classify_news(self):
        news = ('Breaking news: The Federal Reserve announced today that interest rates '
                'will remain unchanged. The decision was made amid growing concerns over '
                'inflation and unemployment rates. Wall Street experts predict volatility. '
                'Stock markets responded positively to the announcement.')
        r = self.tc.classify(news)
        self.assertIsInstance(r, TextClassification)

    def test_classify_conversation(self):
        chat = 'Привет! Как дела?\n- Нормально, спасибо! А ты?\n- Тоже хорошо!'
        r = self.tc.classify(chat)
        self.assertIsInstance(r, TextClassification)

    def test_classify_science(self):
        text = ('The experimental results demonstrate that the quantum decoherence '
                'rate depends on the temperature gradient. Our hypothesis was confirmed '
                'by statistical analysis with p-value < 0.01.')
        r = self.tc.classify(text)
        self.assertIsInstance(r, TextClassification)

    def test_classify_empty(self):
        r = self.tc.classify('')
        self.assertIsInstance(r, TextClassification)

    def test_classify_with_source_hint(self):
        r = self.tc.classify('some text', source_hint='github.com/repo')
        self.assertIsInstance(r, TextClassification)

    def test_classify_batch(self):
        texts = [
            'def foo(): pass',
            'Breaking news today',
            'Hello how are you?',
        ]
        results = self.tc.classify_batch(texts)
        self.assertEqual(len(results), 3)
        self.assertTrue(all(isinstance(r, TextClassification) for r in results))

    def test_classify_batch_with_hints(self):
        texts = ['code here', 'news here']
        hints = ['github.com', 'bbc.com']
        results = self.tc.classify_batch(texts, source_hints=hints)
        self.assertEqual(len(results), 2)


# ═══════════════════════════════════════════════════════════════════════════
# 2. perception/document_parser.py
# ═══════════════════════════════════════════════════════════════════════════
from perception.document_parser import DocumentParser, ParsedDocument


class TestParsedDocument(unittest.TestCase):
    def test_create(self):
        doc = ParsedDocument('test.txt', 'content here')
        self.assertEqual(doc.path, 'test.txt')
        self.assertEqual(doc.text, 'content here')

    def test_to_dict(self):
        doc = ParsedDocument('doc.pdf', 'text', metadata={'author': 'test'}, pages=5)
        d = doc.to_dict()
        self.assertEqual(d['path'], 'doc.pdf')
        self.assertIn('text_preview', d)


class TestDocumentParser(unittest.TestCase):
    def setUp(self):
        self.dp = DocumentParser()

    def test_parse_txt_file(self):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          encoding='utf-8')
        tmp.write('Hello world test content')
        tmp.close()
        try:
            result = self.dp.parse(tmp.name)
            self.assertIsInstance(result, ParsedDocument)
            self.assertIn('Hello', result.text)
        finally:
            os.unlink(tmp.name)

    def test_parse_nonexistent(self):
        result = self.dp.parse('/nonexistent/file.txt')
        self.assertIsNone(result)

    def test_parse_directory(self):
        tmp_dir = tempfile.mkdtemp()
        with open(os.path.join(tmp_dir, 'file1.txt'), 'w', encoding='utf-8') as f:
            f.write('content 1')
        with open(os.path.join(tmp_dir, 'file2.txt'), 'w', encoding='utf-8') as f:
            f.write('content 2')
        results = self.dp.parse_directory(tmp_dir)
        self.assertIsInstance(results, list)

    def test_parse_py_file(self):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False,
                                          encoding='utf-8')
        tmp.write('# Python file\ndef main():\n    print("hello")\n')
        tmp.close()
        try:
            result = self.dp.parse(tmp.name)
            if result:
                self.assertIn('def main', result.text)
        finally:
            os.unlink(tmp.name)


# ═══════════════════════════════════════════════════════════════════════════
# 3. perception/perception_layer.py
# ═══════════════════════════════════════════════════════════════════════════
from perception.perception_layer import PerceptionLayer


class TestPerceptionLayer(unittest.TestCase):
    def test_create_minimal(self):
        pl = PerceptionLayer()
        self.assertIsNotNone(pl)

    def test_classify_text(self):
        pl = PerceptionLayer(text_classifier=TextClassifier())
        result = pl.classify_text('def foo(): pass')
        self.assertIsNotNone(result)

    def test_read_file(self):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          encoding='utf-8')
        tmp.write('test content for perception')
        tmp.close()
        try:
            mock_reader = MagicMock(return_value='test content for perception')
            pl = PerceptionLayer(file_reader=mock_reader)
            result = pl.read_file(tmp.name)
            self.assertIsNotNone(result)
        finally:
            os.unlink(tmp.name)

    def test_receive_message(self):
        pl = PerceptionLayer()
        result = pl.receive_message('Hello agent', sender='user')
        self.assertIsNotNone(result)

    def test_get_data_empty(self):
        pl = PerceptionLayer()
        data = pl.get_data()
        self.assertIsInstance(data, dict)

    def test_collect(self):
        pl = PerceptionLayer()
        result = pl.collect({})
        self.assertIsInstance(result, dict)

    def test_parse_document_txt(self):
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False,
                                          encoding='utf-8')
        tmp.write('document content')
        tmp.close()
        try:
            dp = DocumentParser()
            pl = PerceptionLayer(document_parser=dp)
            result = pl.parse_document(tmp.name)
            self.assertIsNotNone(result)
        finally:
            os.unlink(tmp.name)


# ═══════════════════════════════════════════════════════════════════════════
# 4. perception/image_recognizer.py (result class only — no API calls)
# ═══════════════════════════════════════════════════════════════════════════
from perception.image_recognizer import ImageRecognitionResult


class TestImageRecognitionResult(unittest.TestCase):
    def test_create(self):
        r = ImageRecognitionResult('img.png', 'a cat', objects=['cat'], confidence=0.95)
        self.assertEqual(r.path, 'img.png')
        self.assertEqual(r.description, 'a cat')

    def test_to_dict(self):
        r = ImageRecognitionResult('img.jpg', 'desc', text_in_image='hello')
        d = r.to_dict()
        self.assertEqual(d['path'], 'img.jpg')
        self.assertEqual(d['text_in_image'], 'hello')


# ═══════════════════════════════════════════════════════════════════════════
# 5. perception/speech_recognizer.py (result class only — no API)
# ═══════════════════════════════════════════════════════════════════════════
from perception.speech_recognizer import TranscriptionResult


class TestTranscriptionResult(unittest.TestCase):
    def test_create(self):
        r = TranscriptionResult('audio.wav', 'hello world', language='en', duration=3.5)
        self.assertEqual(r.text, 'hello world')
        self.assertEqual(r.language, 'en')

    def test_to_dict(self):
        r = TranscriptionResult('a.mp3', 'text', confidence=0.8)
        d = r.to_dict()
        self.assertIn('text', d)
        self.assertIn('confidence', d)


# ═══════════════════════════════════════════════════════════════════════════
# 6. knowledge/source_backends.py — structure and simple API tests
# ═══════════════════════════════════════════════════════════════════════════
from knowledge.source_backends import (
    WikipediaBackend, RSSBackend, WeatherBackend,
    HackerNewsBackend, PyPIBackend, GutenbergBackend, ArXivBackend
)


class TestWikipediaBackend(unittest.TestCase):
    def test_create(self):
        wb = WikipediaBackend(lang='en')
        self.assertIsNotNone(wb)

    def test_create_ru(self):
        wb = WikipediaBackend(lang='ru')
        self.assertIsNotNone(wb)


class TestRSSBackend(unittest.TestCase):
    def test_create(self):
        rss = RSSBackend()
        self.assertIsNotNone(rss)

    def test_get_presets(self):
        rss = RSSBackend()
        presets = rss.get_presets('tech')
        self.assertIsInstance(presets, list)


class TestWeatherBackend(unittest.TestCase):
    def test_create(self):
        wb = WeatherBackend()
        self.assertIsNotNone(wb)


class TestHackerNewsBackend(unittest.TestCase):
    def test_create(self):
        hn = HackerNewsBackend()
        self.assertIsNotNone(hn)


class TestPyPIBackend(unittest.TestCase):
    def test_create(self):
        pypi = PyPIBackend()
        self.assertIsNotNone(pypi)


class TestGutenbergBackend(unittest.TestCase):
    def test_create(self):
        gb = GutenbergBackend()
        self.assertIsNotNone(gb)


class TestArXivBackend(unittest.TestCase):
    def test_create(self):
        arxiv = ArXivBackend()
        self.assertIsNotNone(arxiv)


if __name__ == '__main__':
    unittest.main()
