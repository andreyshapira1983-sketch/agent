class KnowledgeGraph:
    def __init__(self):
        self.entities = {}  # Словарь для хранения сущностей
        self.relationships = {}  # Словарь для хранения отношений в формате {from_id: {to_id: relationship_type}}

    def add_entity(self, entity_id, properties):
        self.entities[entity_id] = properties
        self.relationships[entity_id] = {}  # Инициализировать пустой словарь для отношений

    def add_relationship(self, from_id, to_id, relationship_type):
        if from_id in self.relationships and to_id in self.entities:
            self.relationships[from_id][to_id] = relationship_type

    def get_related_entities(self, entity_id):
        return {to_id: self.entities[to_id] for to_id in self.relationships.get(entity_id, {})}