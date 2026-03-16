# Добавляем функцию для голосового ответа
from src.tools.speech import synthesize_speech

def respond_with_voice(text):
    audio_file_path = synthesize_speech(text)
    # Код для отправки голосового сообщения через Telegram API
    send_audio_message(audio_file_path)
