"""Tests for hardware/hardware_layer.py — HardwareInteractionLayer, HardwareMetrics, ResourceThreshold."""
import importlib
import time
import threading
from unittest.mock import patch, MagicMock
import pytest

from hardware.hardware_layer import (
    HardwareMetrics,
    ResourceThreshold,
    HardwareInteractionLayer,
)


# ── HardwareMetrics ───────────────────────────────────────────────────────────

class TestHardwareMetrics:
    def test_defaults(self):
        m = HardwareMetrics()
        assert m.cpu_percent == 0.0
        assert m.memory_percent == 0.0
        assert m.memory_used_mb == 0.0
        assert m.memory_total_mb == 0.0
        assert m.disk_percent == 0.0
        assert m.disk_free_gb == 0.0
        assert m.gpu_percent is None
        assert m.gpu_memory_percent is None
        assert m.net_sent_mb == 0.0
        assert m.net_recv_mb == 0.0
        assert isinstance(m.timestamp, float)

    def test_to_dict(self):
        m = HardwareMetrics()
        m.cpu_percent = 45.2
        m.memory_percent = 60.1
        m.memory_used_mb = 8192.456
        m.memory_total_mb = 16384.789
        m.disk_percent = 72.3
        m.disk_free_gb = 120.456
        m.net_sent_mb = 5.678
        m.net_recv_mb = 12.345
        d = m.to_dict()
        assert d['cpu_percent'] == 45.2
        assert d['memory_percent'] == 60.1
        assert d['memory_used_mb'] == 8192.5
        assert d['memory_total_mb'] == 16384.8
        assert d['disk_percent'] == 72.3
        assert d['disk_free_gb'] == 120.46
        assert d['gpu_percent'] is None
        assert d['net_sent_mb'] == 5.68
        assert d['net_recv_mb'] == 12.35
        assert 'timestamp' in d


# ── ResourceThreshold ────────────────────────────────────────────────────────

class TestResourceThreshold:
    def test_attributes(self):
        t = ResourceThreshold('cpu_percent', warn_at=80.0, critical_at=95.0)
        assert t.resource == 'cpu_percent'
        assert t.warn_at == 80.0
        assert t.critical_at == 95.0


# ── HardwareInteractionLayer ─────────────────────────────────────────────────

class TestHardwareInteractionLayer:

    def _make_layer(self, monitoring=None):
        return HardwareInteractionLayer(monitoring=monitoring, poll_interval=0.01)

    # ── check_resource ────────────────────────────────────────────────────

    def test_check_resource_ok(self):
        h = self._make_layer()
        assert h.check_resource('cpu_percent', 50.0) == 'ok'

    def test_check_resource_warn(self):
        h = self._make_layer()
        assert h.check_resource('cpu_percent', 90.0) == 'warn'

    def test_check_resource_critical(self):
        h = self._make_layer()
        assert h.check_resource('cpu_percent', 98.0) == 'critical'

    def test_check_resource_unknown(self):
        h = self._make_layer()
        assert h.check_resource('unknown_resource', 99.0) == 'ok'

    def test_check_memory_warn(self):
        h = self._make_layer()
        assert h.check_resource('memory_percent', 89.0) == 'warn'

    def test_check_disk_critical(self):
        h = self._make_layer()
        assert h.check_resource('disk_percent', 96.0) == 'critical'

    # ── collect (mocked psutil) ────────────────────────────────────────────

    def test_collect_with_psutil(self):
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 25.0
        mem = MagicMock()
        mem.percent = 50.0
        mem.used = 8 * 1_048_576    # 8 MB
        mem.total = 16 * 1_048_576  # 16 MB
        mock_psutil.virtual_memory.return_value = mem
        disk = MagicMock()
        disk.percent = 40.0
        disk.free = 100 * 1_073_741_824  # 100 GB
        mock_psutil.disk_usage.return_value = disk
        net = MagicMock()
        net.bytes_sent = 5 * 1_048_576
        net.bytes_recv = 10 * 1_048_576
        mock_psutil.net_io_counters.return_value = net

        h = self._make_layer()
        original_import = importlib.import_module
        def side_effect(name):
            if name == 'psutil':
                return mock_psutil
            return original_import(name)
        with patch('importlib.import_module', side_effect=side_effect):
            m = h.collect()

        assert m.cpu_percent == 25.0
        assert m.memory_percent == 50.0
        assert m.memory_used_mb == 8.0
        assert m.memory_total_mb == 16.0
        assert m.disk_percent == 40.0
        assert m.disk_free_gb == 100.0
        assert h.get_last() is m
        assert len(h._history) == 1

    def test_collect_without_psutil(self):
        h = self._make_layer()
        original_import = importlib.import_module
        def side_effect(name):
            if name == 'psutil':
                raise ImportError('psutil')
            return original_import(name)
        with patch('importlib.import_module', side_effect=side_effect):
            m = h.collect()
        assert m.cpu_percent == 0.0
        assert h.get_last() is m

    def test_collect_psutil_exception(self):
        h = self._make_layer()
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.side_effect = RuntimeError("boom")
        original_import = importlib.import_module
        def side_effect(name):
            if name == 'psutil':
                return mock_psutil
            return original_import(name)
        with patch('importlib.import_module', side_effect=side_effect):
            m = h.collect()
        assert m.cpu_percent == 0.0

    def test_collect_with_gpu(self):
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mem = MagicMock(percent=30.0, used=4*1_048_576, total=16*1_048_576)
        mock_psutil.virtual_memory.return_value = mem
        disk = MagicMock(percent=20.0, free=200*1_073_741_824)
        mock_psutil.disk_usage.return_value = disk
        net = MagicMock(bytes_sent=0, bytes_recv=0)
        mock_psutil.net_io_counters.return_value = net

        h = self._make_layer()
        original_import = importlib.import_module
        def side_effect(name):
            if name == 'psutil':
                return mock_psutil
            return original_import(name)
        with patch('importlib.import_module', side_effect=side_effect):
            m = h.collect()
        assert m.cpu_percent == 10.0

    def test_history_trimming(self):
        h = self._make_layer()
        # Manually add 1001 entries
        for _ in range(1001):
            m = HardwareMetrics()
            h._history.append(m)
        h._last_metrics = m
        # Next collect should trim
        original_import = importlib.import_module
        def _skip_psutil(name):
            if name == 'psutil':
                raise ImportError('psutil')
            return original_import(name)
        with patch('importlib.import_module', side_effect=_skip_psutil):
            h.collect()
        assert len(h._history) <= 502  # 500 + 1 from collect

    # ── get_last ─────────────────────────────────────────────────────────

    def test_get_last_none(self):
        h = self._make_layer()
        assert h.get_last() is None

    # ── is_overloaded ────────────────────────────────────────────────────

    def test_is_overloaded_false(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 50.0
        m.memory_percent = 50.0
        m.disk_percent = 50.0
        h._last_metrics = m
        assert not h.is_overloaded()

    def test_is_overloaded_true_cpu(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 98.0
        m.memory_percent = 50.0
        m.disk_percent = 50.0
        h._last_metrics = m
        assert h.is_overloaded()

    def test_is_overloaded_true_memory(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 50.0
        m.memory_percent = 98.0
        m.disk_percent = 50.0
        h._last_metrics = m
        assert h.is_overloaded()

    def test_is_overloaded_true_disk(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 50.0
        m.memory_percent = 50.0
        m.disk_percent = 96.0
        h._last_metrics = m
        assert h.is_overloaded()

    def test_is_overloaded_collects_if_no_metrics(self):
        h = self._make_layer()
        original_import = importlib.import_module
        def _skip_psutil(name):
            if name == 'psutil':
                raise ImportError('psutil')
            return original_import(name)
        with patch('importlib.import_module', side_effect=_skip_psutil):
            result = h.is_overloaded()
        assert not result

    # ── has_capacity_for ─────────────────────────────────────────────────

    def test_has_capacity_true(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 30.0
        m.memory_used_mb = 4000.0
        m.memory_total_mb = 16000.0
        h._last_metrics = m
        assert h.has_capacity_for(required_memory_mb=1000, required_cpu_percent=20)

    def test_has_capacity_false_memory(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 30.0
        m.memory_used_mb = 15000.0
        m.memory_total_mb = 16000.0
        h._last_metrics = m
        assert not h.has_capacity_for(required_memory_mb=5000)

    def test_has_capacity_false_cpu(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 95.0
        m.memory_used_mb = 1000.0
        m.memory_total_mb = 16000.0
        h._last_metrics = m
        assert not h.has_capacity_for(required_cpu_percent=10)

    # ── system_info ──────────────────────────────────────────────────────

    def test_system_info(self):
        h = self._make_layer()
        info = h.system_info()
        assert 'os' in info
        assert 'python' in info
        assert 'hostname' in info

    # ── list_processes ────────────────────────────────────────────────────

    def test_list_processes_no_psutil(self):
        h = self._make_layer()
        original_import = importlib.import_module
        def _skip_psutil(name):
            if name == 'psutil':
                raise ImportError('psutil')
            return original_import(name)
        with patch('importlib.import_module', side_effect=_skip_psutil):
            result = h.list_processes()
        assert result == []

    def test_list_processes_with_psutil(self):
        mock_psutil = MagicMock()
        p1 = MagicMock()
        p1.info = {'pid': 1, 'name': 'test', 'cpu_percent': 10.0, 'memory_percent': 5.0}
        p2 = MagicMock()
        p2.info = {'pid': 2, 'name': 'test2', 'cpu_percent': 20.0, 'memory_percent': 3.0}
        mock_psutil.process_iter.return_value = [p1, p2]

        h = self._make_layer()
        with patch('importlib.import_module', return_value=mock_psutil):
            result = h.list_processes(n=5)
        assert len(result) == 2
        assert result[0]['cpu_percent'] >= result[1]['cpu_percent']

    # ── start/stop monitoring ────────────────────────────────────────────

    def test_start_stop_monitoring(self):
        h = self._make_layer()  # poll_interval=0.01
        original_import = importlib.import_module
        def _skip_psutil(name):
            if name == 'psutil':
                raise ImportError('psutil')
            return original_import(name)
        with patch('importlib.import_module', side_effect=_skip_psutil):
            h.start_monitoring()
            assert h._running
            # Starting again should be no-op
            h.start_monitoring()
            time.sleep(0.05)
            h.stop_monitoring()
            assert not h._running

    # ── get_alerts ────────────────────────────────────────────────────────

    def test_get_alerts_empty(self):
        h = self._make_layer()
        assert h.get_alerts() == []

    def test_alerts_generated_on_warn(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 92.0
        m.memory_percent = 50.0
        m.disk_percent = 50.0
        h._check_thresholds(m)
        alerts = h.get_alerts()
        assert len(alerts) >= 1
        assert alerts[0]['resource'] == 'cpu_percent'
        assert alerts[0]['level'] == 'warn'

    def test_alerts_trimmed_at_200(self):
        h = self._make_layer()
        h._alerts = [{'x': i} for i in range(201)]
        m = HardwareMetrics()
        m.cpu_percent = 98.0
        m.memory_percent = 50.0
        m.disk_percent = 50.0
        h._check_thresholds(m)
        assert len(h._alerts) <= 201

    def test_alerts_with_monitoring(self):
        mon = MagicMock()
        h = self._make_layer(monitoring=mon)
        m = HardwareMetrics()
        m.cpu_percent = 98.0
        m.memory_percent = 50.0
        m.disk_percent = 50.0
        h._check_thresholds(m)
        assert mon.warning.called

    # ── summary ──────────────────────────────────────────────────────────

    def test_summary_no_data(self):
        h = self._make_layer()
        s = h.summary()
        assert s['status'] == 'no data'

    def test_summary_ok(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 30.0
        m.memory_percent = 40.0
        m.disk_percent = 50.0
        h._last_metrics = m
        s = h.summary()
        assert s['status'] == 'ok'
        assert s['cpu_percent'] == 30.0

    def test_summary_critical(self):
        h = self._make_layer()
        m = HardwareMetrics()
        m.cpu_percent = 98.0
        m.memory_percent = 40.0
        m.disk_percent = 50.0
        h._last_metrics = m
        s = h.summary()
        assert s['status'] == 'critical'

    # ── resource_trend_summary ───────────────────────────────────────────

    def test_trend_empty(self):
        h = self._make_layer()
        t = h.resource_trend_summary()
        assert t['window_size'] == 0

    def test_trend_stable(self):
        h = self._make_layer()
        for _ in range(20):
            m = HardwareMetrics()
            m.cpu_percent = 50.0
            m.memory_percent = 60.0
            m.disk_percent = 30.0
            h._history.append(m)
        t = h.resource_trend_summary(window=20)
        assert t['window_size'] == 20
        assert t['cpu']['trend'] == 'stable'
        assert t['memory']['trend'] == 'stable'
        assert t['breach_events'] == 0

    def test_trend_rising(self):
        h = self._make_layer()
        for i in range(20):
            m = HardwareMetrics()
            m.cpu_percent = 40.0 + i * 3  # rising
            m.memory_percent = 50.0
            m.disk_percent = 30.0
            h._history.append(m)
        t = h.resource_trend_summary(window=20)
        assert t['cpu']['trend'] == 'rising'

    def test_trend_falling(self):
        h = self._make_layer()
        for i in range(20):
            m = HardwareMetrics()
            m.cpu_percent = 80.0 - i * 3  # falling
            m.memory_percent = 50.0
            m.disk_percent = 30.0
            h._history.append(m)
        t = h.resource_trend_summary(window=20)
        assert t['cpu']['trend'] == 'falling'

    def test_trend_breach_events(self):
        h = self._make_layer()
        for _ in range(10):
            m = HardwareMetrics()
            m.cpu_percent = 92.0  # warn
            m.memory_percent = 50.0
            m.disk_percent = 50.0
            h._history.append(m)
        t = h.resource_trend_summary(window=10)
        assert t['breach_events'] == 10

    # ── _log ─────────────────────────────────────────────────────────────

    def test_log_with_monitoring(self):
        mon = MagicMock()
        h = self._make_layer(monitoring=mon)
        h._log("test message")
        mon.info.assert_called_with("test message", source='hardware_layer')

    def test_log_without_monitoring(self, capsys):
        h = self._make_layer()
        h._log("test message")
        out = capsys.readouterr().out
        assert "test message" in out
