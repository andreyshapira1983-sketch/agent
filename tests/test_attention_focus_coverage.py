import unittest
from unittest.mock import patch

from attention.attention_focus import (
    AttentionItem,
    AttentionMode,
    AttentionFocusManager,
    AttentionFocus,
)


class _Monitoring:
    def __init__(self):
        self.events = []

    def info(self, message, source=''):
        self.events.append((message, source))


class _Goal:
    def __init__(self, description):
        self.description = description


class _GoalManager:
    def __init__(self, active=None):
        self._active = active

    def get_active(self):
        return self._active


class AttentionFocusCoverageTests(unittest.TestCase):
    def test_attention_item_clamps_and_to_dict(self):
        item = AttentionItem(
            item_id='x1',
            description='Test item',
            source='unit',
            urgency=2.0,
            importance=-1.0,
            context={'k': 1},
        )
        self.assertEqual(item.urgency, 1.0)
        self.assertEqual(item.importance, 0.0)
        self.assertAlmostEqual(item.priority_score, 0.6)
        d = item.to_dict()
        self.assertEqual(d['item_id'], 'x1')
        self.assertEqual(d['priority_score'], 0.6)
        self.assertFalse(d['attended'])

    def test_signal_queue_sort_and_critical_reactive(self):
        mon = _Monitoring()
        mgr = AttentionFocusManager(monitoring=mon)
        a = mgr.signal('low prio', urgency=0.1, importance=0.1)
        b = mgr.signal('high prio', urgency=0.9, importance=0.9)
        self.assertEqual(mgr.summary()['queue_size'], 2)
        snap = mgr.queue_snapshot()
        self.assertEqual(snap[0]['item_id'], b.item_id)
        self.assertEqual(snap[1]['item_id'], a.item_id)

        c = mgr.signal_critical('critical event')
        self.assertEqual(c.item_id, 'att0003')
        self.assertEqual(mgr.get_mode(), AttentionMode.REACTIVE)
        self.assertTrue(any('Режим внимания' in e[0] for e in mon.events))

    def test_noise_filter_drops_signal(self):
        mon = _Monitoring()
        mgr = AttentionFocusManager(monitoring=mon)
        mgr.add_noise_keyword('spam')
        item = mgr.signal('This is SPAM message', source='chat')
        self.assertEqual(item.source, 'chat')
        self.assertEqual(mgr.summary()['queue_size'], 0)
        self.assertTrue(any('отфильтрован как шум' in e[0] for e in mon.events))

    def test_update_focus_goal_rerank_attend_and_attend_all(self):
        goal_mgr = _GoalManager(active=_Goal('market analysis profit'))
        mgr = AttentionFocusManager(goal_manager=goal_mgr)

        # Добавляем несколько сигналов так, чтобы релевантный цели мог подняться после rerank
        i1 = mgr.signal('profit forecast report', urgency=0.4, importance=0.5)
        mgr.signal('random telemetry ping', urgency=0.6, importance=0.6)
        i3 = mgr.signal('market trend analysis', urgency=0.3, importance=0.4)
        i4 = mgr.signal('minor note', urgency=0.2, importance=0.2)

        focus = mgr.update_focus()
        self.assertEqual(len(focus), 3)
        self.assertEqual(mgr.get_mode(), AttentionMode.FOCUSED)
        focus_ids = {x.item_id for x in focus}
        self.assertIn(i1.item_id, focus_ids)
        self.assertIn(i3.item_id, focus_ids)

        # attend no-op для несуществующего id
        mgr.attend('missing-id')
        self.assertEqual(mgr.summary()['total_processed'], 0)

        # attend конкретного
        mgr.attend(i1.item_id)
        self.assertEqual(mgr.summary()['total_processed'], 1)

        # update_focus должен убрать attended и добрать из очереди
        focus2 = mgr.update_focus()
        self.assertEqual(len(focus2), 3)
        self.assertTrue(any(x.item_id == i4.item_id for x in focus2))

        mgr.attend_all()
        self.assertEqual(len(mgr.get_focus()), 0)
        self.assertGreaterEqual(mgr.summary()['total_processed'], 3)

    def test_set_mode_and_idle_transition_and_logging_without_monitor(self):
        mgr = AttentionFocusManager(monitoring=None)
        with patch('builtins.print') as p:
            mgr.set_mode(AttentionMode.SCANNING)
            # Повторная установка того же режима не должна логировать второй раз
            mgr.set_mode(AttentionMode.SCANNING)
            # Пустые queue/focus + не-idle режим → переход в idle
            mgr.update_focus()
        printed = [c.args[0] for c in p.call_args_list]
        self.assertTrue(any('idle' in m for m in printed))
        self.assertEqual(mgr.get_mode(), AttentionMode.IDLE)

    def test_attention_focus_empty_state(self):
        af = AttentionFocus()
        state = af.get_focus()
        self.assertEqual(state['focused_topics'], [])
        self.assertEqual(state['focus_score'], 0.0)
        self.assertTrue(state['distracted'])

    def test_attention_focus_update_boost_decay_top3_and_cap(self):
        af = AttentionFocus()

        # stop-words и короткие слова должны быть отфильтрованы
        af.update_focus('the and to at')
        self.assertEqual(af.get_focus()['focused_topics'], [])

        # Поднимаем фокус одного топика до >= 0.3 (не distracted)
        af.update_focus('market strategy execution', context={'x': 1})
        af.update_focus('market strategy execution')
        st = af.update_focus('market strategy execution')
        self.assertFalse(st['distracted'])

        # Создаём более 3 тем, чтобы проверить top-3
        af.update_focus('finance pricing margin operations')
        af.update_focus('logistics inventory demand planning')
        top = af.get_focus()['focused_topics']
        self.assertLessEqual(len(top), 3)

        # Дожимаем weight cap до 1.0
        for _ in range(20):
            af.update_focus('market')
        self.assertLessEqual(af.get_focus()['focus_score'], 1.0)

        # Проверяем удаление очень малых весов через decay (<0.01)
        for _ in range(120):
            af.update_focus('singleanchor')
        # После длинного decay старые темы должны вымыться, остаётся только anchor
        topics = af.get_focus()['focused_topics']
        self.assertIn('singleanchor', topics)


if __name__ == '__main__':
    unittest.main()
