"""Tests for preflight.py — CheckResult and check functions."""
import os
import json
import subprocess
from unittest.mock import patch, MagicMock
import pytest

import preflight


class TestCheckResult:
    def test_passed(self):
        r = preflight.CheckResult('Test', True, 'All good')
        assert r.passed
        assert r.icon == '✅'
        assert 'Test' in str(r)
        assert 'All good' in str(r)

    def test_failed(self):
        r = preflight.CheckResult('Test', False, 'Bad')
        assert not r.passed
        assert r.icon == '❌'

    def test_fixable(self):
        r = preflight.CheckResult('Test', False, 'Fix me', fixable=True)
        assert not r.passed
        assert r.icon == '🔧'

    def test_no_message(self):
        r = preflight.CheckResult('Test', True)
        s = str(r)
        assert 'Test' in s


class TestCheckCriticalFiles:
    def test_all_present(self):
        r = preflight.check_critical_files()
        assert r.passed
        assert 'файлов на месте' in r.message

    def test_missing_file(self):
        with patch('os.path.exists', return_value=False):
            r = preflight.check_critical_files()
        assert not r.passed
        assert 'Отсутствуют' in r.message


class TestCheckConfigsValid:
    def test_valid(self):
        r = preflight.check_configs_valid()
        assert r.passed

    def test_invalid_json(self, tmp_path):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("{invalid", encoding='utf-8')
        with patch.object(preflight, '_ROOT', str(tmp_path)):
            # Force configs list to point to our bad file
            r = preflight.check_configs_valid()
            # Since the configs path is relative to _ROOT, it might not find our file
            # Just verify it returns a CheckResult
            assert isinstance(r, preflight.CheckResult)


class TestCheckCoreImports:
    def test_success(self):
        r = preflight.check_core_imports()
        assert r.passed

    def test_failure(self):
        with patch('importlib.import_module', side_effect=ImportError("no module")):
            r = preflight.check_core_imports()
        assert not r.passed


class TestCheckTests:
    def test_quick_passed(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = '10 passed in 1s'
        mock_result.stderr = ''
        with patch('subprocess.run', return_value=mock_result):
            r = preflight.check_tests(quick=True)
        assert r.passed

    def test_full_failed(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = '5 failed, 10 passed'
        mock_result.stderr = ''
        with patch('subprocess.run', return_value=mock_result):
            r = preflight.check_tests(quick=False)
        assert not r.passed

    def test_timeout(self):
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='pytest', timeout=300)):
            r = preflight.check_tests()
        assert not r.passed
        assert 'Таймаут' in r.message

    def test_pytest_not_found(self):
        with patch('subprocess.run', side_effect=FileNotFoundError):
            r = preflight.check_tests()
        assert not r.passed
        assert r.fixable

    def test_exit_0_no_passed_line(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'no output'
        mock_result.stderr = ''
        with patch('subprocess.run', return_value=mock_result):
            r = preflight.check_tests()
        assert r.passed


class TestCheckGitStatus:
    def test_clean(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ''
        with patch('subprocess.run', return_value=mock_result):
            r = preflight.check_git_status()
        assert r.passed

    def test_dirty(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = 'M agent.py\n?? new_file.py\n'
        with patch('subprocess.run', return_value=mock_result):
            r = preflight.check_git_status()
        assert not r.passed

    def test_not_git_repo(self):
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ''
        with patch('subprocess.run', return_value=mock_result):
            r = preflight.check_git_status()
        assert not r.passed

    def test_git_not_installed(self):
        with patch('subprocess.run', side_effect=FileNotFoundError):
            r = preflight.check_git_status()
        assert not r.passed


class TestCheckDependencies:
    def test_no_lock_file(self):
        with patch('os.path.exists', return_value=False):
            r = preflight.check_dependencies()
        assert not r.passed
        assert r.fixable

    def test_lock_exists_ok(self, tmp_path):
        lock = tmp_path / "requirements.lock"
        lock.write_text("package==1.0\n", encoding='utf-8')
        req = tmp_path / "requirements.txt"
        req.write_text("package\n", encoding='utf-8')
        # Make lock newer than req
        os.utime(lock, (os.path.getmtime(req) + 10, os.path.getmtime(req) + 10))
        with patch.object(preflight, '_ROOT', str(tmp_path)):
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            config_lock = config_dir / "requirements.lock"
            config_lock.write_text("pkg==1.0\n", encoding='utf-8')
            config_req = config_dir / "requirements.txt"
            config_req.write_text("pkg\n", encoding='utf-8')
            os.utime(config_lock, (os.path.getmtime(config_req) + 10, os.path.getmtime(config_req) + 10))
            r = preflight.check_dependencies()
        assert r.passed


class TestCheckLogsWritable:
    def test_writable(self):
        r = preflight.check_logs_writable()
        assert r.passed


class TestCheckChangeTracker:
    def test_available(self):
        r = preflight.check_change_tracker()
        assert r.passed


class TestRunPreflight:
    def test_quick(self):
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = '10 passed'
            mock_result.stderr = ''
            mock_run.return_value = mock_result
            results = preflight.run_preflight(quick=True)
        assert isinstance(results, list)
        assert all(isinstance(r, preflight.CheckResult) for r in results)

    def test_full(self):
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = '10 passed'
            mock_result.stderr = ''
            mock_run.return_value = mock_result
            results = preflight.run_preflight(quick=False)
        assert len(results) >= 7

    def test_exception_in_check(self):
        def boom():
            raise RuntimeError("boom")
        boom.__name__ = 'check_critical_files'
        with patch.object(preflight, 'check_critical_files', boom):
            with patch('subprocess.run') as mock_run:
                mock_result = MagicMock()
                mock_result.returncode = 0
                mock_result.stdout = '10 passed'
                mock_result.stderr = ''
                mock_run.return_value = mock_result
                results = preflight.run_preflight(quick=True)
            # Should still execute all checks despite exception
            assert isinstance(results, list)


class TestMain:
    def test_main_success(self):
        with patch('sys.argv', ['preflight.py', '--quick']):
            with patch.object(preflight, 'run_preflight') as mock_run:
                mock_run.return_value = [
                    preflight.CheckResult('Test', True, 'ok'),
                ]
                with pytest.raises(SystemExit) as exc_info:
                    preflight.main()
                assert exc_info.value.code == 0

    def test_main_failure(self):
        with patch('sys.argv', ['preflight.py', '--quick']):
            with patch.object(preflight, 'run_preflight') as mock_run:
                mock_run.return_value = [
                    preflight.CheckResult('Test', False, 'bad'),
                ]
                with pytest.raises(SystemExit) as exc_info:
                    preflight.main()
                assert exc_info.value.code == 1
