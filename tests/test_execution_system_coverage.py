"""Тесты для execution/execution_system.py — ExecutionSystem, TaskStatus, ExecutionTask."""

import pytest
from unittest.mock import MagicMock, patch
from execution.execution_system import TaskStatus, ExecutionTask, ExecutionSystem


# ── ExecutionTask ─────────────────────────────────────────────────────────────

class TestExecutionTask:
    def test_init_defaults(self):
        t = ExecutionTask("t1", "echo hello")
        assert t.task_id == "t1"
        assert t.command == "echo hello"
        assert t.task_type == "command"
        assert t.metadata == {}
        assert t.status == TaskStatus.PENDING
        assert t.stdout is None
        assert t.stderr is None
        assert t.returncode is None
        assert t.error is None

    def test_init_with_metadata(self):
        t = ExecutionTask("t2", "ls", task_type="script", metadata={"k": "v"})
        assert t.task_type == "script"
        assert t.metadata == {"k": "v"}

    def test_to_dict(self):
        t = ExecutionTask("t3", "cmd")
        t.status = TaskStatus.SUCCESS
        t.stdout = "out"
        t.returncode = 0
        d = t.to_dict()
        assert d["task_id"] == "t3"
        assert d["status"] == "success"
        assert d["stdout"] == "out"
        assert d["returncode"] == 0


# ── TaskStatus ────────────────────────────────────────────────────────────────

class TestTaskStatus:
    def test_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.SUCCESS.value == "success"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.REJECTED.value == "rejected"
        assert TaskStatus.TIMEOUT.value == "timeout"


# ── ExecutionSystem ───────────────────────────────────────────────────────────

class TestExecutionSystem:
    def _make_system(self, **kwargs):
        defaults = dict(
            human_approval=None,
            monitoring=None,
            working_dir="/tmp",
            timeout_default=30,
            safe_mode=True,
        )
        defaults.update(kwargs)
        return ExecutionSystem(**defaults)

    def test_safe_mode_readonly(self):
        es = self._make_system(safe_mode=True)
        assert es.safe_mode is True
        # safe_mode не должен быть изменяем через обычный атрибут
        with pytest.raises(AttributeError):
            es.safe_mode = False

    def test_get_task_empty(self):
        es = self._make_system()
        assert es.get_task("nope") is None

    def test_get_all_tasks_empty(self):
        es = self._make_system()
        assert es.get_all_tasks() == []

    def test_get_failed_tasks_empty(self):
        es = self._make_system()
        assert es.get_failed_tasks() == []

    # ── submit: rejected by human approval ──

    def test_submit_rejected_by_human_approval(self):
        approval = MagicMock()
        approval.approve_execution.return_value = False
        es = self._make_system(human_approval=approval, safe_mode=True)
        task = es.submit("rm -rf /", require_approval=True)
        assert task.status == TaskStatus.REJECTED

    # ── submit: approved, command validator blocks ──

    def test_submit_blocked_by_command_validator(self):
        """Команда заблокирована через CommandValidator."""
        validator_mock = MagicMock(return_value=(False, "Blocked by policy"))
        with patch("execution.command_gateway.CommandGateway.get_instance"), \
             patch("tools.tool_layer.CommandValidator.validate", validator_mock):
            es = self._make_system(safe_mode=False)
            task = es.submit("something")
            assert task.status == TaskStatus.REJECTED
            assert task.error  # has error message

    # ── submit: success via gateway ──

    def test_submit_success(self):
        gw_result = MagicMock()
        gw_result.allowed = True
        gw_result.stdout = "hello"
        gw_result.stderr = ""
        gw_result.returncode = 0
        gw_result.reject_reason = ""

        gw_instance = MagicMock()
        gw_instance.execute.return_value = gw_result

        validator_mock = MagicMock(return_value=(True, ""))
        with patch("execution.command_gateway.CommandGateway.get_instance", return_value=gw_instance), \
             patch("tools.tool_layer.CommandValidator.validate", validator_mock):
            es = self._make_system(safe_mode=False)
            task = es.submit("echo hello")
            assert task.status == TaskStatus.SUCCESS
            assert task.stdout == "hello"
            assert task.returncode == 0

    # ── submit: gateway returns failure ──

    def test_submit_gateway_failure(self):
        gw_result = MagicMock()
        gw_result.allowed = True
        gw_result.stdout = ""
        gw_result.stderr = "err"
        gw_result.returncode = 1
        gw_result.reject_reason = ""

        gw_instance = MagicMock()
        gw_instance.execute.return_value = gw_result

        validator_mock = MagicMock(return_value=(True, ""))
        with patch("execution.command_gateway.CommandGateway.get_instance", return_value=gw_instance), \
             patch("tools.tool_layer.CommandValidator.validate", validator_mock):
            es = self._make_system(safe_mode=False)
            task = es.submit("failing_cmd")
            assert task.status == TaskStatus.FAILED

    # ── submit: gateway rejects ──

    def test_submit_gateway_rejects(self):
        gw_result = MagicMock()
        gw_result.allowed = False
        gw_result.reject_reason = "blacklisted"

        gw_instance = MagicMock()
        gw_instance.execute.return_value = gw_result

        validator_mock = MagicMock(return_value=(True, ""))
        with patch("execution.command_gateway.CommandGateway.get_instance", return_value=gw_instance), \
             patch("tools.tool_layer.CommandValidator.validate", validator_mock):
            es = self._make_system(safe_mode=False)
            task = es.submit("badcmd")
            assert task.status == TaskStatus.REJECTED

    # ── submit: timeout ──

    def test_submit_timeout(self):
        gw_result = MagicMock()
        gw_result.allowed = True
        gw_result.stdout = ""
        gw_result.stderr = ""
        gw_result.returncode = -1
        gw_result.reject_reason = "\u0422\u0430\u0439\u043c\u0430\u0443\u0442 30\u0441"

        gw_instance = MagicMock()
        gw_instance.execute.return_value = gw_result

        validator_mock = MagicMock(return_value=(True, ""))
        with patch("execution.command_gateway.CommandGateway.get_instance", return_value=gw_instance), \
             patch("tools.tool_layer.CommandValidator.validate", validator_mock):
            es = self._make_system(safe_mode=False)
            task = es.submit("slow_cmd")
            assert task.status == TaskStatus.TIMEOUT

    # ── submit: exception in _run ──

    def test_submit_exception(self):
        validator_mock = MagicMock(return_value=(True, ""))
        with patch("tools.tool_layer.CommandValidator.validate", validator_mock), \
             patch("execution.command_gateway.CommandGateway.get_instance", side_effect=RuntimeError("boom")):
            es = self._make_system(safe_mode=False)
            task = es.submit("cmd")
            assert task.status == TaskStatus.FAILED
            assert "boom" in (task.error or "")

    # ── run_command ──

    def test_run_command_delegates(self):
        es = self._make_system()
        with patch.object(es, "submit", return_value="fake") as mock_sub:
            result = es.run_command("echo hi", timeout=10)
            mock_sub.assert_called_once_with("echo hi", task_type="command", timeout=10)
            assert result == "fake"

    # ── run_script ──

    def test_run_script(self):
        es = self._make_system()
        with patch.object(es, "submit", return_value="fake") as mock_sub:
            es.run_script("test.py", args=["--verbose"])
            call_args = mock_sub.call_args
            assert "test.py" in call_args[0][0]
            assert "--verbose" in call_args[0][0]
            assert call_args[1]["task_type"] == "script"

    # ── start_service / stop_service ──

    def test_start_service_invalid_name(self):
        es = self._make_system()
        task = es.start_service("bad name!")
        assert task.status == TaskStatus.REJECTED
        assert "Invalid" in (task.error or "")

    def test_stop_service_invalid_name(self):
        es = self._make_system()
        task = es.stop_service("foo bar;")
        assert task.status == TaskStatus.REJECTED

    def test_start_service_valid(self):
        es = self._make_system()
        with patch.object(es, "submit", return_value="fake") as mock_sub:
            result = es.start_service("myservice")
            assert result == "fake"
            mock_sub.assert_called_once()
            assert mock_sub.call_args[1]["task_type"] == "service"

    def test_stop_service_valid(self):
        es = self._make_system()
        with patch.object(es, "submit", return_value="fake"):
            result = es.stop_service("myservice")
            assert result == "fake"

    # ── deploy ──

    def test_deploy_requires_approval(self):
        es = self._make_system()
        with patch.object(es, "submit", return_value="fake") as mock_sub:
            es.deploy("kubectl apply -f x.yaml", metadata={"env": "prod"})
            assert mock_sub.call_args[1]["require_approval"] is True

    # ── schedule ──

    def test_schedule_returns_task_id(self):
        es = self._make_system()
        with patch.object(es, "_run"):  # prevent actual execution
            tid = es.schedule("echo scheduled", delay_seconds=0)
            assert isinstance(tid, str)
            assert tid in es._tasks

    # ── task history ──

    def test_get_task_after_submit(self):
        es = self._make_system()
        with patch.object(es, "_run"):
            task = es.submit("echo hi")
            fetched = es.get_task(task.task_id)
            assert fetched is task

    def test_get_all_tasks(self):
        es = self._make_system()
        with patch.object(es, "_run"):
            es.submit("cmd1")
            es.submit("cmd2")
            all_tasks = es.get_all_tasks()
            assert len(all_tasks) == 2

    def test_get_failed_tasks_filters(self):
        es = self._make_system()
        with patch.object(es, "_run"):
            t1 = es.submit("cmd1")
            t2 = es.submit("cmd2")
            t1.status = TaskStatus.SUCCESS
            t2.status = TaskStatus.FAILED
            failed = es.get_failed_tasks()
            assert len(failed) == 1
            assert failed[0]["status"] == "failed"

    # ── _is_dangerous ──

    def test_is_dangerous_detects_rm(self):
        es = self._make_system()
        with patch("tools.tool_layer.CommandValidator.validate", return_value=(True, "")):
            assert es._is_dangerous("rm -rf /") is True

    def test_is_dangerous_safe_command(self):
        es = self._make_system()
        with patch("tools.tool_layer.CommandValidator.validate", return_value=(True, "")):
            assert es._is_dangerous("echo hello") is False

    def test_is_dangerous_blocked_validator(self):
        es = self._make_system()
        with patch("tools.tool_layer.CommandValidator.validate", return_value=(False, "nope")):
            assert es._is_dangerous("whatever") is True

    # ── _is_windows ──

    def test_is_windows(self):
        es = self._make_system()
        import os as _os
        assert es._is_windows() == (_os.name == "nt")

    # ── _log with monitoring ──

    def test_log_with_monitoring(self):
        mon = MagicMock()
        es = self._make_system(monitoring=mon)
        task = ExecutionTask("t", "cmd")
        es._log(task)
        mon.log_execution.assert_called_once()

    def test_log_without_monitoring(self):
        es = self._make_system(monitoring=None)
        task = ExecutionTask("t", "cmd")
        es._log(task)  # no error
