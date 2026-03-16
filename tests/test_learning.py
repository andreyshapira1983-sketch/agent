# tests/test_learning.py

from src.learning.learning_manager import LearningManager

def test_learning_manager():
    lm = LearningManager()
    
    def condition_example(action_data):
        return action_data['action'] == 'test_action'

    lm.add_rule(condition_example, {'some_parameter': 'adjusted_value'})
    
    action_data = {'action': 'test_action', 'some_parameter': 'initial_value'}
    adjusted_action_data = lm.apply_rules(action_data)

    assert adjusted_action_data['some_parameter'] == 'adjusted_value'
    
    action_data_no_match = {'action': 'other_action', 'some_parameter': 'initial_value'}
    unchanged_action_data = lm.apply_rules(action_data_no_match)

    assert unchanged_action_data['some_parameter'] == 'initial_value'