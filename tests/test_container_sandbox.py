"""Тесты ContainerSandbox и container-path в SandboxLayer.run_code().

Покрытие:
  - ContainerSandbox.is_available(): Docker absent / present / image missing / кэш
  - ContainerSandbox.run(): OK / timeout / OSError / nonzero exit / context / network
  - ContainerSandbox.reset_cache()
  - SandboxLayer.run_code(): container path vs subprocess fallback
  - Docker security flags (--read-only, --network none, --no-new-privileges и т.д.)
"""

import subprocess
import time
from unittest.mock import MagicMock, patch, call

import pytest

from environment.sandbox import (
    ContainerSandbox,
    SandboxLayer,
    SandboxResult,
    _DEFAULT_LIMITS,
    _SANDBOX_IMAGE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# ContainerSandbox.is_available()
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsAvailable:

    def test_docker_binary_not_found(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox.shutil.which', return_value=None):
            assert cs.is_available() is False

    def test_docker_present_image_exists(self):
        cs = ContainerSandbox()
        mock_result = MagicMock(returncode=0)
        with patch('environment.sandbox.shutil.which', return_value='/usr/bin/docker'), \
             patch('environment.sandbox._subprocess.run', return_value=mock_result):
            assert cs.is_available() is True

    def test_docker_present_image_missing(self):
        cs = ContainerSandbox()
        mock_result = MagicMock(returncode=1)
        with patch('environment.sandbox.shutil.which', return_value='/usr/bin/docker'), \
             patch('environment.sandbox._subprocess.run', return_value=mock_result):
            assert cs.is_available() is False

    def test_oserror_returns_false(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox.shutil.which', return_value='/usr/bin/docker'), \
             patch('environment.sandbox._subprocess.run', side_effect=OSError('boom')):
            assert cs.is_available() is False

    def test_timeout_expired_returns_false(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox.shutil.which', return_value='/usr/bin/docker'), \
             patch('environment.sandbox._subprocess.run',
                   side_effect=subprocess.TimeoutExpired('docker', 10)):
            assert cs.is_available() is False

    def test_result_is_cached(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox.shutil.which', return_value=None) as mock_which:
            assert cs.is_available() is False
            # Второй вызов не должен заходить в shutil.which
            assert cs.is_available() is False
            mock_which.assert_called_once()

    def test_reset_cache_clears(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox.shutil.which', return_value=None):
            cs.is_available()
        assert cs._available is False
        cs.reset_cache()
        assert cs._available is None


# ═══════════════════════════════════════════════════════════════════════════════
# ContainerSandbox.run()
# ═══════════════════════════════════════════════════════════════════════════════

class TestContainerRun:

    def _make_proc(self, returncode=0, stdout='hello', stderr=''):
        p = MagicMock()
        p.returncode = returncode
        p.stdout = stdout
        p.stderr = stderr
        return p

    def test_successful_execution(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox._subprocess.run',
                   return_value=self._make_proc(0, 'ok', '')):
            success, stdout, stderr, dur = cs.run('print(1)')
        assert success is True
        assert stdout == 'ok'
        assert stderr == ''
        assert dur >= 0

    def test_nonzero_exit(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox._subprocess.run',
                   return_value=self._make_proc(1, '', 'error text')):
            success, _stdout, stderr, _ = cs.run('bad code')
        assert success is False
        assert stderr == 'error text'

    def test_timeout_expired(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox._subprocess.run',
                   side_effect=subprocess.TimeoutExpired('docker', 30)):
            success, _stdout, stderr, _ = cs.run('while True: pass', timeout=5)
        assert success is False
        assert 'timeout' in stderr.lower()

    def test_oserror_handling(self):
        cs = ContainerSandbox()
        with patch('environment.sandbox._subprocess.run',
                   side_effect=OSError('Docker crashed')):
            success, _stdout, stderr, _ = cs.run('print(1)')
        assert success is False
        assert 'Docker' in stderr

    def test_context_passed_safely(self):
        cs = ContainerSandbox()
        captured_cmd = []

        def capture_run(cmd, **_kwargs):
            captured_cmd.extend(cmd)
            return self._make_proc()

        with patch('environment.sandbox._subprocess.run', side_effect=capture_run):
            cs.run('print(x)', context={'x': 42, 'obj': object()})

        # Контекст-JSON передаётся последним аргументом
        ctx_arg = captured_cmd[-1]
        import json
        ctx = json.loads(ctx_arg)
        assert ctx['x'] == 42
        # object() не сериализуется — должен быть отброшен
        assert 'obj' not in ctx

    def test_default_timeout_used(self):
        cs = ContainerSandbox(default_timeout=15)
        with patch('environment.sandbox._subprocess.run',
                   return_value=self._make_proc()) as mock_run:
            cs.run('pass')
        _, kwargs = mock_run.call_args
        assert kwargs['timeout'] == 15

    def test_explicit_timeout_overrides_default(self):
        cs = ContainerSandbox(default_timeout=15)
        with patch('environment.sandbox._subprocess.run',
                   return_value=self._make_proc()) as mock_run:
            cs.run('pass', timeout=5)
        _, kwargs = mock_run.call_args
        assert kwargs['timeout'] == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Docker security flags
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityFlags:
    """Проверяем, что docker run использует все обязательные security-флаги."""

    def test_all_hardening_flags_present(self):
        cs = ContainerSandbox()
        captured_cmd = []

        def capture_run(cmd, **_kwargs):
            captured_cmd.extend(cmd)
            p = MagicMock()
            p.returncode = 0
            p.stdout = ''
            p.stderr = ''
            return p

        with patch('environment.sandbox._subprocess.run', side_effect=capture_run):
            cs.run('pass')

        cmd_str = ' '.join(captured_cmd)
        assert '--read-only' in cmd_str
        assert '--network none' in cmd_str
        assert '--no-new-privileges' in cmd_str
        assert '--user nobody' in cmd_str
        assert '--memory 256m' in cmd_str
        assert '--cpus 1' in cmd_str
        assert '--pids-limit 64' in cmd_str
        assert '--tmpfs' in cmd_str
        assert '--rm' in cmd_str

    def test_network_bridge_when_enabled(self):
        cs = ContainerSandbox(network=True)
        captured_cmd = []

        def capture_run(cmd, **_kwargs):
            captured_cmd.extend(cmd)
            p = MagicMock()
            p.returncode = 0
            p.stdout = ''
            p.stderr = ''
            return p

        with patch('environment.sandbox._subprocess.run', side_effect=capture_run):
            cs.run('pass')

        cmd_str = ' '.join(captured_cmd)
        assert '--network bridge' in cmd_str

    def test_stdin_flag_present(self):
        cs = ContainerSandbox()
        captured_cmd = []

        def capture_run(cmd, **_kwargs):
            captured_cmd.extend(cmd)
            p = MagicMock()
            p.returncode = 0
            p.stdout = ''
            p.stderr = ''
            return p

        with patch('environment.sandbox._subprocess.run', side_effect=capture_run):
            cs.run('pass')

        assert '-i' in captured_cmd

    def test_code_passed_via_stdin(self):
        cs = ContainerSandbox()

        def capture_run(_cmd, **_kwargs):
            p = MagicMock()
            p.returncode = 0
            p.stdout = ''
            p.stderr = ''
            return p

        with patch('environment.sandbox._subprocess.run', side_effect=capture_run) as mock_run:
            cs.run('print("hello")')

        _, kwargs = mock_run.call_args
        assert kwargs['input'] == 'print("hello")'


# ═══════════════════════════════════════════════════════════════════════════════
# ContainerSandbox defaults
# ═══════════════════════════════════════════════════════════════════════════════

class TestDefaults:

    def test_default_image(self):
        cs = ContainerSandbox()
        assert cs.image == _SANDBOX_IMAGE

    def test_custom_limits(self):
        cs = ContainerSandbox(memory='512m', cpus='2', pids_limit='128')
        assert cs.memory == '512m'
        assert cs.cpus == '2'
        assert cs.pids_limit == '128'


# ═══════════════════════════════════════════════════════════════════════════════
# SandboxLayer.run_code() — container path vs subprocess fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestSandboxLayerContainerPath:

    def _make_container(self, available=True, success=True, stdout='', stderr=''):
        cs = MagicMock(spec=ContainerSandbox)
        cs.is_available.return_value = available
        cs.run.return_value = (success, stdout, stderr, 0.05)
        return cs

    def test_container_used_when_available(self):
        cs = self._make_container(available=True, success=True, stdout='42')
        layer = SandboxLayer(container_sandbox=cs)
        run = layer.run_code('print(42)')
        cs.run.assert_called_once()
        assert run.verdict == SandboxResult.SAFE
        assert run.stdout == '42'

    def test_container_error_sets_error_verdict(self):
        cs = self._make_container(available=True, success=False, stderr='oom killed')
        layer = SandboxLayer(container_sandbox=cs)
        run = layer.run_code('x = 1')
        assert run.verdict == SandboxResult.ERROR
        assert run.error is not None
        assert 'oom killed' in run.error

    def test_subprocess_fallback_when_container_unavailable(self):
        cs = self._make_container(available=False)
        layer = SandboxLayer(container_sandbox=cs)
        # Мокаем subprocess fallback, чтобы не запускать реальный процесс
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = 'fallback'
        fake_proc.stderr = ''
        with patch('subprocess.run', return_value=fake_proc), \
             patch('safety.secrets_proxy.safe_env', return_value={}):
            run = layer.run_code('print(1)')
        # Container.run НЕ вызывается — упали на fallback
        cs.run.assert_not_called()
        assert run.stdout == 'fallback'

    def test_static_checks_block_before_container(self):
        """Статические проверки (import os) должны блокировать ДО контейнера."""
        cs = self._make_container(available=True)
        layer = SandboxLayer(container_sandbox=cs)
        run = layer.run_code('import os; os.system("rm -rf /")')
        assert run.verdict == SandboxResult.UNSAFE
        cs.run.assert_not_called()

    def test_dunder_block_before_container(self):
        cs = self._make_container(available=True)
        layer = SandboxLayer(container_sandbox=cs)
        run = layer.run_code('().__class__.__bases__[0].__subclasses__()')
        assert run.verdict == SandboxResult.UNSAFE
        cs.run.assert_not_called()

    def test_size_check_blocks_before_container(self):
        cs = self._make_container(available=True)
        layer = SandboxLayer(container_sandbox=cs)
        run = layer.run_code('x = 1\n' * 200_000)
        assert run.verdict == SandboxResult.UNSAFE
        cs.run.assert_not_called()

    def test_container_risky_when_side_effects_detected(self):
        """Код с побочными эффектами (но не blocked) → RISKY через контейнер."""
        cs = self._make_container(available=True, success=True, stdout='done')
        layer = SandboxLayer(container_sandbox=cs)
        # Мокаем _detect_side_effects: возвращает non-blocked effect
        # (blocked содержат 'ОС', 'процесс', 'сетевые', 'файлов')
        with patch.object(layer, '_detect_side_effects',
                          return_value=['изменение состояния']):
            run = layer.run_code('x = 1')
        assert run.verdict == SandboxResult.RISKY
        cs.run.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# SandboxLayer default ContainerSandbox creation
# ═══════════════════════════════════════════════════════════════════════════════

class TestSandboxLayerInit:

    def test_default_container_sandbox_created(self):
        layer = SandboxLayer()
        assert isinstance(layer.container_sandbox, ContainerSandbox)

    def test_custom_container_sandbox_injected(self):
        cs = ContainerSandbox(image='custom-img')
        layer = SandboxLayer(container_sandbox=cs)
        assert layer.container_sandbox is cs
        assert layer.container_sandbox.image == 'custom-img'

    def test_none_creates_default(self):
        layer = SandboxLayer(container_sandbox=None)
        assert isinstance(layer.container_sandbox, ContainerSandbox)
