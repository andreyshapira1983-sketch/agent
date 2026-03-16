import pytest
from src.learning.reflection import ReflectionEngine

@pytest.fixture
def reflection_engine():
    engine = ReflectionEngine()
    yield engine

def test_record_action(reflection_engine):
    reflection_engine.record_action("test_action", {"input": "test"}, "output_value")
    assert len(reflection_engine.action_log) == 1
    assert reflection_engine.action_log[0]['action_type'] == "test_action"

def test_record_outcome(reflection_engine):
    reflection_engine.record_action("test_action", {"input": "test"}, "output_value")
    reflection_engine.record_outcome(1, "success")
    assert reflection_engine.action_log[0]['outcome'] == "success"

def test_analyze(reflection_engine):
    reflection_engine.record_action("test_action", {"input": "test"}, "output_value")
    reflection_engine.record_outcome(1, "success")
    insights = reflection_engine.analyze()
    assert len(insights) == 1
    assert "Action 1 was a success" in insights[0]