"""DataClassifier — assigns public / private / sensitive / secret."""
from __future__ import annotations

from core.data_classifier import DataClass, classify


class TestEdgeCases:
    def test_empty_text_falls_back_to_source_default(self):
        assert classify("", source="web").cls is DataClass.PUBLIC
        assert classify("", source="file").cls is DataClass.PRIVATE
