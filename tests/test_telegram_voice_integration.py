import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from communication.telegram_bot import TelegramBot


class _FakeCore:
    def converse(self, text, system=None, history=None):
        _ = (text, system, history)
        return (
            "Суть сообщения кратка.\n"
            "Success Rate: 0.5\n"
            "Действуй так: начни с ассортимента и маржи."
        )


class _FakeAsr:
    def __init__(self, recognized_text):
        self.recognized_text = recognized_text
        self.calls = []

    def transcribe(self, audio_path):
        self.calls.append(audio_path)
        return SimpleNamespace(text=self.recognized_text)


class _FakeTts:
    def __init__(self):
        self.synth_calls = []
        self.cleanup_calls = []

    def synthesize(self, text):
        self.synth_calls.append(text)
        return "fake-response.opus"

    def cleanup(self, audio_path):
        self.cleanup_calls.append(audio_path)


class _TestableTelegramBot(TelegramBot):
    """Тестовый адаптер: открывает публичный вход в voice-путь без прямого доступа к protected API."""

    def handle_voice_for_test(self, chat_id: int, voice_obj: dict, actor_id: str, username: str):
        return self._handle_voice(chat_id=chat_id, voice_obj=voice_obj, actor_id=actor_id, username=username)


class TelegramVoiceIntegrationTests(unittest.TestCase):
    def _make_temp_audio(self):
        fh = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        fh.write(b"fake")
        fh.close()
        return fh.name

    def test_voice_path_sends_sanitized_text_to_tts(self):
        asr = _FakeAsr("Да алё привет, как управлять магазином с ИИ?")
        tts = _FakeTts()
        bot = _TestableTelegramBot(
            token="test-token",
            allowed_chat_ids=[1],
            cognitive_core=_FakeCore(),
            speech_recognizer=asr,
            speech_synthesizer=tts,
        )

        temp_audio = self._make_temp_audio()
        sent_messages = []

        with patch.object(bot, "send", side_effect=lambda chat_id, text, parse_mode="HTML": sent_messages.append((chat_id, text, parse_mode))):
            with patch.object(bot, "_download_file", return_value=temp_audio):
                with patch.object(bot, "send_voice", return_value=True) as send_voice_mock:
                    bot.handle_voice_for_test(
                        chat_id=1,
                        voice_obj={"file_id": "abc123"},
                        actor_id="1",
                        username="andre",
                    )

        self.assertEqual(len(asr.calls), 1)
        self.assertFalse(os.path.exists(temp_audio), "Временный аудиофайл должен удаляться")
        self.assertEqual(len(tts.synth_calls), 1)
        self.assertEqual(tts.synth_calls[0], "Действуй так: начни с ассортимента и маржи.")
        self.assertEqual(tts.cleanup_calls, ["fake-response.opus"])
        send_voice_mock.assert_called_once_with(1, "fake-response.opus")
        self.assertEqual(sent_messages, [], "При успешной отправке голоса текстовый fallback не нужен")

    def test_voice_path_falls_back_to_text_when_voice_send_fails(self):
        asr = _FakeAsr("Как управлять магазином с ИИ?")
        tts = _FakeTts()
        bot = _TestableTelegramBot(
            token="test-token",
            allowed_chat_ids=[1],
            cognitive_core=_FakeCore(),
            speech_recognizer=asr,
            speech_synthesizer=tts,
        )

        temp_audio = self._make_temp_audio()
        sent_messages = []

        with patch.object(bot, "send", side_effect=lambda chat_id, text, parse_mode="HTML": sent_messages.append((chat_id, text, parse_mode))):
            with patch.object(bot, "_download_file", return_value=temp_audio):
                with patch.object(bot, "send_voice", return_value=False):
                    bot.handle_voice_for_test(
                        chat_id=1,
                        voice_obj={"file_id": "abc123"},
                        actor_id="1",
                        username="andre",
                    )

        self.assertEqual(len(tts.synth_calls), 1)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0][0], 1)
        self.assertIn("Действуй так: начни с ассортимента и маржи.", sent_messages[0][1])
        self.assertNotIn("Success Rate", sent_messages[0][1])


if __name__ == "__main__":
    unittest.main()
