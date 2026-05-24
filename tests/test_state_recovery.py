"""tests/test_state_recovery.py — тесты State & Recovery (S_State)

Сценарии:
    1. TaskStore CRUD и статусный автомат
    2. IdempotencyStore: кэш, TTL, purge
    3. RecoveryManager: симуляция краша и восстановление
    4. ToolExecutor: idempotency_store подключён и работает
    5. Crash + Recovery end-to-end сценарий
"""

from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import shutil
import tempfile
import time
from pathlib import Path

from brain.planner import PlanCheckpointStore, Plan
from brain.state import (
    IdempotencyStore, RecoveryInfo, RecoveryManager,
    TaskSession, TaskStatus, TaskStore,
)
from tools.base import ToolResult
from tools.executor import ToolExecutor
from tools.registry import ToolRegistry


def section(name: str) -> None:
    print(f"\n{'='*55}")
    print(f"  {name}")
    print("="*55)


tmp = Path(tempfile.mkdtemp())

try:
    # ─────────────────────────────────────────────────────────────
    # 1. TaskStore CRUD
    # ─────────────────────────────────────────────────────────────
    section("1. TaskStore — CRUD и статусный автомат")

    ts = TaskStore(tmp / "tasks.db")

    t1 = ts.create("telegram:100", "user:alice", "Сделать презентацию для клиента")
    print(f"  created: {t1.task_id[:8]} status={t1.status.value}")
    assert t1.status == TaskStatus.PENDING

    ts.update_status(t1.task_id, TaskStatus.RUNNING)
    ts.save_context(t1.task_id, {"step": 2, "data": "analysis done"})
    ts.set_plan(t1.task_id, "plan-001")

    loaded = ts.get(t1.task_id)
    assert loaded is not None
    assert loaded.status == TaskStatus.RUNNING
    assert loaded.plan_job_id == "plan-001"
    ctx = loaded.get_context()
    assert ctx["step"] == 2
    print(f"  loaded: status={loaded.status.value} plan={loaded.plan_job_id}")

    # Завершение
    ts.complete(t1.task_id)
    done = ts.get(t1.task_id)
    assert done.status == TaskStatus.COMPLETED
    assert done.completed_at is not None
    print(f"  completed: {done.completed_at.strftime('%H:%M:%S')}")

    # Провал
    t2 = ts.create("telegram:100", "user:alice", "Найти подрядчика")
    ts.update_status(t2.task_id, TaskStatus.RUNNING)
    ts.fail(t2.task_id, "API timeout after 3 retries")
    failed = ts.get(t2.task_id)
    assert failed.status == TaskStatus.FAILED
    assert "timeout" in failed.error
    print(f"  failed: error='{failed.error[:40]}'")

    # Статистика
    stats = ts.stats()
    print(f"  stats: {stats}")
    assert stats.get("completed", 0) == 1
    assert stats.get("failed", 0) == 1

    print("  ✅ TaskStore OK")

    # ─────────────────────────────────────────────────────────────
    # 2. IdempotencyStore
    # ─────────────────────────────────────────────────────────────
    section("2. IdempotencyStore — кэш и TTL")

    idm = IdempotencyStore(tmp / "idem.db", ttl_hours=1)

    key1 = idm.make_key("task-A", 5, "email_tool", {"to": "ceo@corp.com", "subject": "Report"})
    key2 = idm.make_key("task-A", 5, "email_tool", {"subject": "Report", "to": "ceo@corp.com"})
    assert key1 == key2, "Должны быть одинаковы (order-independent)"
    print(f"  key determinism: {key1[:16]}... == {key2[:16]}... ✅")

    # Нет записи
    assert idm.check(key1) is None
    print("  empty check: None ✅")

    # Сохраняем успешный результат
    r = ToolResult(
        tool_name="email_tool",
        success=True,
        output={"sent": True, "message_id": "msg-987"},
    )
    idm.save(key1, "task-A", 5, "email_tool", r)
    assert idm.count() == 1

    cached = idm.check(key1)
    assert cached is not None
    tr = cached.to_tool_result()
    assert tr.success is True
    assert tr.output["sent"] is True
    assert tr.metadata.get("_from_cache") is True
    print(f"  cached result: {tr.output} ✅")

    # Не кэшируем ошибки
    fail_r = ToolResult(tool_name="email_tool", success=False, output=None, error="SMTP timeout")
    fail_key = idm.make_key("task-A", 6, "email_tool", {})
    idm.save(fail_key, "task-A", 6, "email_tool", fail_r)
    assert idm.check(fail_key) is None, "Ошибки не должны кэшироваться"
    print("  error not cached ✅")

    # Purge expired
    purged = idm.purge_expired()
    assert purged == 0  # не прошёл срок
    print(f"  purge (0 expired): {purged} ✅")

    print("  ✅ IdempotencyStore OK")

    # ─────────────────────────────────────────────────────────────
    # 3. RecoveryManager — симуляция краша
    # ─────────────────────────────────────────────────────────────
    section("3. RecoveryManager — симуляция краша")

    # Создаём свежую БД с незавершёнными задачами (имитируем краш)
    ts2 = TaskStore(tmp / "tasks2.db")

    crash_t1 = ts2.create("telegram:200", "user:bob", "Проанализировать рынок")
    ts2.update_status(crash_t1.task_id, TaskStatus.RUNNING)
    ts2.set_plan(crash_t1.task_id, "plan-crash-001")

    crash_t2 = ts2.create("telegram:201", "user:carol", "Написать статью")
    ts2.update_status(crash_t2.task_id, TaskStatus.RUNNING)

    crash_t3 = ts2.create("telegram:202", "user:dave", "Уже завершённая задача")
    ts2.complete(crash_t3.task_id)  # эта не должна попасть в recovery

    plans2 = PlanCheckpointStore(tmp / "plans2.db")
    mgr = RecoveryManager(ts2, plans2)

    recovered = mgr.recover()
    print(f"  recovered count: {len(recovered)} (expected 2)")
    assert len(recovered) == 2

    for ri in recovered:
        print(f"  - {ri.summary()}")
        assert isinstance(ri, RecoveryInfo)
        assert ri.task.status == TaskStatus.RECOVERING, f"Expected RECOVERING, got {ri.task.status}"

    # finalize одну
    mgr.finalize(crash_t1.task_id)
    ft = ts2.get(crash_t1.task_id)
    assert ft.status == TaskStatus.RUNNING
    print(f"  finalized {crash_t1.task_id[:8]}: status={ft.status.value} ✅")

    # abandon другую
    mgr.abandon(crash_t2.task_id, "user decided not to continue")
    at = ts2.get(crash_t2.task_id)
    assert at.status == TaskStatus.FAILED
    print(f"  abandoned {crash_t2.task_id[:8]}: status={at.status.value} ✅")

    # Второй запуск recovery — пусто (нет новых running задач)
    recovered2 = mgr.recover()
    print(f"  2nd recover: {len(recovered2)} tasks (expected 0 running, 1 recovering)")
    # После finalize есть 1 RUNNING снова (crash_t1 был finalized → RUNNING)
    assert len(recovered2) == 1   # только crash_t1 снова в RUNNING

    print("  ✅ RecoveryManager OK")

    # ─────────────────────────────────────────────────────────────
    # 4. ToolExecutor + IdempotencyStore: интеграция
    # ─────────────────────────────────────────────────────────────
    section("4. ToolExecutor — idempotency integration")

    from tools.builtins.calculator import CalculatorTool

    registry = ToolRegistry()
    idm2 = IdempotencyStore(tmp / "idem2.db")
    registry.register(CalculatorTool())
    executor = ToolExecutor(registry, idempotency_store=idm2)

    # Первый вызов — реальное вычисление
    r1 = executor.run(
        "calculator",
        {"expression": "100 * 42"},
        task_id="task-idem-test",
        step_id=7,
    )
    assert r1.success
    print(f"  1st call result: {r1.output}")
    assert not r1.metadata.get("_from_cache")

    # Второй вызов — из кэша
    r2 = executor.run(
        "calculator",
        {"expression": "100 * 42"},
        task_id="task-idem-test",
        step_id=7,
    )
    assert r2.success
    assert r2.metadata.get("_from_cache") is True
    print(f"  2nd call (cached): {r2.output} _from_cache={r2.metadata['_from_cache']} ✅")

    assert str(r1.output) == str(r2.output)
    print("  results match ✅")

    # Другой step_id — не кэшируется
    r3 = executor.run(
        "calculator",
        {"expression": "100 * 42"},
        task_id="task-idem-test",
        step_id=8,  # другой шаг
    )
    assert not r3.metadata.get("_from_cache")
    print(f"  different step_id: NOT cached ✅")

    print("  ✅ ToolExecutor+Idempotency OK")

    # ─────────────────────────────────────────────────────────────
    # 5. End-to-end: crash → restart → recovery
    # ─────────────────────────────────────────────────────────────
    section("5. End-to-end: crash simulation")

    # === Сессия 1 (до краша) ===
    ts3 = TaskStore(tmp / "tasks3.db")
    idm3 = IdempotencyStore(tmp / "idem3.db")
    plans3 = PlanCheckpointStore(tmp / "plans3.db")

    task_live = ts3.create("telegram:500", "user:eve", "Написать коммерческое предложение")
    ts3.update_status(task_live.task_id, TaskStatus.RUNNING)

    # Шаг 1 выполнен и сохранён в idempotency
    ikey_s1 = idm3.make_key(task_live.task_id, 1, "calculator", {"expression": "1+1"})
    idm3.save(ikey_s1, task_live.task_id, 1, "calculator",
              ToolResult(tool_name="calculator", success=True, output="2"))

    # === КРАШ ===
    ts3.close()
    idm3.close()
    plans3.close()
    print("  [crash] process killed")

    # === Сессия 2 (после перезапуска) ===
    ts3 = TaskStore(tmp / "tasks3.db")
    idm3 = IdempotencyStore(tmp / "idem3.db")
    plans3 = PlanCheckpointStore(tmp / "plans3.db")
    mgr3 = RecoveryManager(ts3, plans3)

    recovered3 = mgr3.recover()
    print(f"  [restart] recovered: {len(recovered3)} task(s)")
    assert len(recovered3) == 1
    assert recovered3[0].task_id == task_live.task_id

    # Шаг 1 не выполняется снова — кэш сработает
    ikey_s1_again = idm3.make_key(task_live.task_id, 1, "calculator", {"expression": "1+1"})
    cached3 = idm3.check(ikey_s1_again)
    assert cached3 is not None, "Step 1 должен быть в кэше после краша"
    print(f"  [restart] step 1 idempotency cached: {cached3.to_tool_result().output} ✅")

    mgr3.finalize(task_live.task_id)
    ts3.close(); idm3.close(); plans3.close()
    print("  ✅ End-to-end crash recovery OK")

    # Close all remaining open stores before cleanup
    ts.close()
    idm.close()
    ts2.close()
    plans2.close()
    idm2.close()

finally:
    shutil.rmtree(tmp)

print("\n\n✅ ALL STATE/RECOVERY TESTS PASSED!")
