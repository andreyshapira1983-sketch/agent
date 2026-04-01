# perception — Perception Layer (Слой 1)
# Восприятие среды: web, файлы, API, документы, изображения, аудио, сообщения.
from .perception_layer import PerceptionLayer
from .document_parser import DocumentParser, ParsedDocument
from .image_recognizer import ImageRecognizer, ImageRecognitionResult
from .speech_recognizer import SpeechRecognizer, TranscriptionResult

__all__ = [
    'PerceptionLayer',
    'DocumentParser', 'ParsedDocument',
    'ImageRecognizer', 'ImageRecognitionResult',
    'SpeechRecognizer', 'TranscriptionResult',
]
