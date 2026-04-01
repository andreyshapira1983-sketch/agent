import unittest

from communication.telegram_bot import TelegramBot


class TelegramDocumentInputTests(unittest.TestCase):
    def test_document_input_explicitly_forbids_reasking_file(self):
        text = TelegramBot._format_document_chat_input(
            file_name='Текстовый документ .json',
            preview='{"a": 1}',
            caption='проверь это',
            pages=1,
        )

        self.assertIn('Пользователь уже приложил документ', text)
        self.assertIn('Не проси прислать файл заново', text)
        self.assertIn('СОДЕРЖИМОЕ ДОКУМЕНТА', text)
        self.assertIn('{"a": 1}', text)


if __name__ == '__main__':
    unittest.main()