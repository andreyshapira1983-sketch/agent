from src.reflection.reflection import ReflectionEngine

def test_reflection_engine():
    engine = ReflectionEngine()
    action_data = {'action': 'test_action', 'success': True, 'expected_result': 'test_result'}
    assert engine.analyze_action(action_data) == 'Success'
    assert engine.self_assessment()['success_rate'] == 100.0  # После одного успешного действия

def test_self_assessment_empty():
    engine = ReflectionEngine()
    assert engine.self_assessment() == {'success_rate': 0.0, 'error_rate': 0.0}  # Проверка на пустой список

def test_action_time_and_error_count():
    engine = ReflectionEngine()
    action_data_success = {'action': 'test_action', 'success': True, 'expected_result': 'test_result'}
    action_data_failure = {'action': 'test_action_fail', 'success': False, 'expected_result': 'test_result'}
    
    engine.analyze_action(action_data_success)
    engine.analyze_action(action_data_failure)

    assert len(engine.action_logs) == 2
    assert engine.action_logs[1]['error_count'] == 1
    assert engine.self_assessment()['error_count'] == 1

def test_sequence_trace_and_previous_action():
    engine = ReflectionEngine()
    engine.analyze_action({'action': 'first', 'success': True})
    engine.analyze_action({'action': 'second', 'success': True})

    trace = engine.get_sequence_trace()
    assert trace == ['first', 'second']
    assert engine.action_logs[1]['previous_action'] == 'first'