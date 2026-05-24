"""tests/tools/test_text_summarizer.py — TextSummarizerTool"""

import pytest
from tools.builtins.text_summarizer import TextSummarizerTool


TEXT_LONG = (
    "Python is a high-level programming language. "
    "It was created by Guido van Rossum and first released in 1991. "
    "Python is widely used in data science, machine learning, and web development. "
    "The language emphasizes code readability and simplicity. "
    "Many companies including Google and Netflix use Python extensively. "
    "Python has a rich ecosystem of libraries and frameworks. "
    "The Python community is one of the largest in the world. "
    "It supports multiple programming paradigms including procedural, OOP, and functional. "
    "Python 3 was released in 2008 and is the current major version. "
    "The language is dynamically typed and has automatic memory management."
)


class TestTextSummarizerTool:
    def setup_method(self):
        self.tool = TextSummarizerTool()

    def test_spec_name(self):
        assert self.tool.spec.name == "text_summarizer"

    def test_spec_not_destructive(self):
        assert self.tool.spec.is_destructive is False

    def test_basic_summary(self):
        r = self.tool.execute(text=TEXT_LONG, max_sentences=3)
        assert r.success is True
        assert isinstance(r.output, list)
        assert len(r.output) <= 3

    def test_default_max_sentences_is_5(self):
        r = self.tool.execute(text=TEXT_LONG)
        assert r.success is True
        assert len(r.output) <= 5

    def test_single_sentence(self):
        r = self.tool.execute(text="Python is great.", max_sentences=3)
        assert r.success is True
        assert len(r.output) == 1

    def test_empty_text(self):
        r = self.tool.execute(text="")
        assert r.success is False

    def test_missing_text_param(self):
        r = self.tool.execute()
        assert r.success is False

    def test_text_too_long(self):
        r = self.tool.execute(text="x" * 60_000)
        assert r.success is False
        assert "long" in r.error.lower()

    def test_max_sentences_too_low(self):
        r = self.tool.execute(text=TEXT_LONG, max_sentences=0)
        assert r.success is False

    def test_max_sentences_too_high(self):
        r = self.tool.execute(text=TEXT_LONG, max_sentences=100)
        assert r.success is False

    def test_metadata_has_counts(self):
        r = self.tool.execute(text=TEXT_LONG, max_sentences=3)
        assert "sentence_count" in r.metadata
        assert "original_length" in r.metadata
        assert r.metadata["original_length"] == len(TEXT_LONG)

    def test_output_sentences_are_strings(self):
        r = self.tool.execute(text=TEXT_LONG, max_sentences=2)
        for s in r.output:
            assert isinstance(s, str)
            assert len(s) > 0

    def test_duration_in_metadata(self):
        r = self.tool.execute(text=TEXT_LONG)
        assert "duration_ms" in r.metadata
