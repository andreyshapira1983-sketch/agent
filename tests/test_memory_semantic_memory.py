import unittest
from src.memory.semantic_memory import SemanticMemory

class TestSemanticMemory(unittest.TestCase):
    def setUp(self):
        self.memory = SemanticMemory()

    def test_add_and_retrieve_entry(self):
        self.memory.add_entry('test_key', 'test_value')
        self.assertEqual(self.memory.retrieve_entry('test_key'), 'test_value')

    def test_find_similar_entries(self):
        # Псевдотест для нахождения похожих записей
        self.assertEqual(self.memory.find_similar_entries('query'), {})

if __name__ == '__main__':
    unittest.main()
