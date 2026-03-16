import pytest
from src.memory.knowledge_graph import KnowledgeGraph

@pytest.fixture
def knowledge_graph():
    kg = KnowledgeGraph()
    yield kg

def test_add_entity(knowledge_graph):
    knowledge_graph.add_entity("Entity1", {"type": "example", "description": "An example entity"})
    assert "Entity1" in knowledge_graph.entities  # Проверяем, что сущность добавлена

def test_add_relationship(knowledge_graph):
    knowledge_graph.add_entity("Entity1", {"type": "example"})
    knowledge_graph.add_entity("Entity2", {"type": "example"})
    knowledge_graph.add_relationship("Entity1", "Entity2", "RELATED_TO")
    related_entities = knowledge_graph.get_related_entities("Entity1")
    assert "Entity2" in related_entities  # Теперь есть одна связанная сущность

def test_get_related_entities(knowledge_graph):
    knowledge_graph.add_entity("Entity1", {"type": "example"})
    knowledge_graph.add_entity("Entity2", {"type": "example"})
    knowledge_graph.add_relationship("Entity1", "Entity2", "RELATED_TO")
    related_entities = knowledge_graph.get_related_entities("Entity1")
    assert related_entities["Entity2"]["type"] == "example"  # Проверяем свойства связанной сущности