# src/reflection/rule_generator.py

class RuleGenerator:
    def __init__(self):
        self.generated_rules = []

    def derive_rule(self, action_data):
        required_keys = ['action', 'expected_result', 'success']
        if not all(k in action_data for k in required_keys):
            return None
        if action_data['success']:
            rule = f"If {action_data['action']} is done, then {action_data['expected_result']} is expected."
            self.generated_rules.append(rule)
            return rule
