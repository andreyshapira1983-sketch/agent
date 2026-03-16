# src/learning/learning_manager.py

class LearningManager:
    def __init__(self):
        self.rules = []

    def add_rule(self, condition, adjustments):
        self.rules.append({'condition': condition, 'adjustments': adjustments})

    def apply_rules(self, action_data):
        for rule in self.rules:
            if rule['condition'](action_data):
                # Применение корректировок к action_data, если правило срабатывает
                for key, value in rule['adjustments'].items():
                    action_data[key] = value
                return action_data  # Возвращаем измененные данные
        return action_data  # Если правила не сработали, возвращаем как есть