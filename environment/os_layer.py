# Operating System Layer — Слой 6
# Архитектура автономного AI-агента
#
# Единый фасад для взаимодействия с ОС: файлы, процессы, пакеты,
# сеть, устройства, сервисы, привилегии.
#
# Делегирует безопасное выполнение через Tool Layer (Слой 5),
# Hardware Layer (Слой 44) и CommandGateway (Слой 8).
# pylint: disable=broad-except

from __future__ import annotations

import os
import platform
import shutil
import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, cast


# ── Модель ОС ────────────────────────────────────────────────────────────────

class OSPlatform(Enum):
    WINDOWS = 'Windows'
    LINUX   = 'Linux'
    MACOS   = 'Darwin'
    UNKNOWN = 'Unknown'


@dataclass(frozen=True)
class OSInfo:
    """Неизменяемый снимок информации об ОС."""
    platform: OSPlatform
    version: str
    architecture: str
    hostname: str
    username: str
    python_version: str
    cpu_count: int
    home_dir: str

    def to_dict(self) -> dict:
        return {
            'platform': self.platform.value,
            'version': self.version,
            'architecture': self.architecture,
            'hostname': self.hostname,
            'username': self.username,
            'python_version': self.python_version,
            'cpu_count': self.cpu_count,
            'home_dir': self.home_dir,
        }


@dataclass
class ServiceInfo:
    """Описание системного сервиса."""
    name: str
    status: str          # running | stopped | unknown
    pid: int | None = None
    start_type: str = ''  # automatic | manual | disabled


@dataclass
class NetworkInterface:
    """Описание сетевого интерфейса."""
    name: str
    ip_addresses: list[str] = field(default_factory=list)
    mac_address: str = ''
    is_up: bool = False
    speed_mbps: int = 0


@dataclass
class DiskInfo:
    """Информация о дисковом томе."""
    mount_point: str
    device: str
    fs_type: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float


@dataclass
class DeviceInfo:
    """Информация о подключённом устройстве (USB и др.)."""
    device_id: str
    name: str
    device_type: str     # usb | serial | bluetooth | other
    vendor: str = ''
    status: str = 'connected'


# ── Основной класс ───────────────────────────────────────────────────────────

class OperatingSystemLayer:
    """
    Operating System Layer — Слой 6.

    Единый фасад для всех OS-операций агента.

    Архитектурные функции (из архитектуры автономного Агента):
        - управление файлами
        - запуск процессов
        - установка программ
        - управление пакетами
        - управление сетью
        - управление устройствами

    Поддержка: Windows, Linux, macOS.

    OWNERSHIP CONTRACT:
        НЕ владеет: Tool Layer, Hardware Layer, CommandGateway.
        Является фасадом — делегирует вызовы специализированным инструментам,
        добавляя кроссплатформенную абстракцию, управление привилегиями,
        сервис-менеджмент и device enumeration.
    """

    def __init__(
        self,
        tool_layer=None,
        hardware=None,
        monitoring=None,
        governance=None,
    ):
        self.tool_layer = tool_layer
        self.hardware = hardware
        self.monitoring = monitoring
        self.governance = governance
        self._os_info: OSInfo | None = None
        self._lock = threading.Lock()

    # ── OS Information ────────────────────────────────────────────────────────

    def get_os_info(self) -> OSInfo:
        """Возвращает информацию о текущей ОС (кешируется)."""
        if self._os_info is None:
            sys_name = platform.system()
            plat = OSPlatform.WINDOWS if sys_name == 'Windows' else \
                   OSPlatform.LINUX if sys_name == 'Linux' else \
                   OSPlatform.MACOS if sys_name == 'Darwin' else \
                   OSPlatform.UNKNOWN
            self._os_info = OSInfo(
                platform=plat,
                version=platform.version(),
                architecture=platform.machine(),
                hostname=platform.node(),
                username=os.environ.get('USER') or os.environ.get('USERNAME', 'unknown'),
                python_version=platform.python_version(),
                cpu_count=os.cpu_count() or 1,
                home_dir=os.path.expanduser('~'),
            )
        return self._os_info

    @property
    def is_windows(self) -> bool:
        return self.get_os_info().platform == OSPlatform.WINDOWS

    @property
    def is_linux(self) -> bool:
        return self.get_os_info().platform == OSPlatform.LINUX

    @property
    def is_macos(self) -> bool:
        return self.get_os_info().platform == OSPlatform.MACOS

    # ── Файловая система ──────────────────────────────────────────────────────

    def file_exists(self, path: str) -> bool:
        """Проверяет существование файла."""
        return os.path.exists(path)

    def get_file_info(self, path: str) -> dict | None:
        """Метаданные файла: размер, время модификации, права."""
        if not os.path.exists(path):
            return None
        stat = os.stat(path)
        return {
            'path': os.path.abspath(path),
            'size_bytes': stat.st_size,
            'modified': time.ctime(stat.st_mtime),
            'created': time.ctime(stat.st_ctime),
            'is_dir': os.path.isdir(path),
            'is_file': os.path.isfile(path),
            'is_symlink': os.path.islink(path),
            'mode': oct(stat.st_mode),
        }

    def disk_usage(self) -> list[DiskInfo]:
        """Информация обо всех дисковых томах."""
        disks = []
        try:
            import psutil
            for part in psutil.disk_partitions(all=False):
                try:
                    usage = psutil.disk_usage(part.mountpoint)
                    disks.append(DiskInfo(
                        mount_point=part.mountpoint,
                        device=part.device,
                        fs_type=part.fstype,
                        total_gb=round(usage.total / (1024 ** 3), 2),
                        used_gb=round(usage.used / (1024 ** 3), 2),
                        free_gb=round(usage.free / (1024 ** 3), 2),
                        percent_used=usage.percent,
                    ))
                except (PermissionError, OSError):
                    continue
        except ImportError:
            # Fallback без psutil
            total, used, free = shutil.disk_usage(os.sep)
            disks.append(DiskInfo(
                mount_point=os.sep,
                device='',
                fs_type='',
                total_gb=round(total / (1024 ** 3), 2),
                used_gb=round(used / (1024 ** 3), 2),
                free_gb=round(free / (1024 ** 3), 2),
                percent_used=round(used / total * 100, 1) if total else 0,
            ))
        return disks

    # ── Процессы ──────────────────────────────────────────────────────────────

    def list_processes(self, name_filter: str = '') -> list[dict]:
        """Список процессов (опционально фильтрованный по имени)."""
        procs = []
        try:
            import psutil
            for p in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent', 'status']):
                try:
                    info = p.info
                    if name_filter and name_filter.lower() not in (info.get('name') or '').lower():
                        continue
                    procs.append({
                        'pid': info['pid'],
                        'name': info.get('name', ''),
                        'cpu_percent': info.get('cpu_percent', 0.0),
                        'memory_percent': round(info.get('memory_percent', 0.0), 2),
                        'status': info.get('status', ''),
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except ImportError:
            self._log("psutil недоступен — list_processes ограничен", level='warning')
        return procs

    def kill_process(self, pid: int) -> dict:
        """Завершение процесса по PID (требует governance-проверку)."""
        if self.governance:
            try:
                gov = self.governance.check(
                    f"os_layer: kill_process pid={pid}",
                    context={'action': 'kill_process', 'pid': pid},
                )
                if not gov.get('allowed', True):
                    return {'ok': False, 'error': f"Governance: {gov.get('reason', 'запрещено')}"}
            except Exception:
                pass

        try:
            import psutil
            proc = psutil.Process(pid)
            proc_name = proc.name()
            proc.terminate()
            proc.wait(timeout=5)
            self._log(f"Процесс {proc_name} (PID {pid}) завершён")
            return {'ok': True, 'name': proc_name, 'pid': pid}
        except ImportError:
            return {'ok': False, 'error': 'psutil недоступен'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Пакеты и программы ────────────────────────────────────────────────────

    def list_installed_packages(self, manager: str = 'pip') -> list[dict]:
        """Список установленных пакетов."""
        if manager == 'pip':
            return self._pip_list()
        if manager == 'npm':
            return self._npm_list()
        return []

    def install_package(self, package: str, manager: str = 'pip',
                        version: str = '') -> dict:
        """Установка пакета (pip, npm)."""
        if self.governance:
            try:
                gov = self.governance.check(
                    f"os_layer: install_package {manager} {package}",
                    context={'action': 'install_package', 'package': package, 'manager': manager},
                )
                if not gov.get('allowed', True):
                    return {'ok': False, 'error': f"Governance: {gov.get('reason', 'запрещено')}"}
            except Exception:
                pass

        spec = f"{package}=={version}" if version else package

        if manager == 'pip':
            return self._run_tool_cmd(f"pip install {spec}")
        elif manager == 'npm':
            return self._run_tool_cmd(f"npm install {spec}")
        return {'ok': False, 'error': f'Менеджер "{manager}" не поддерживается'}

    def uninstall_package(self, package: str, manager: str = 'pip') -> dict:
        """Удаление пакета."""
        if manager == 'pip':
            return self._run_tool_cmd(f"pip uninstall -y {package}")
        elif manager == 'npm':
            return self._run_tool_cmd(f"npm uninstall {package}")
        return {'ok': False, 'error': f'Менеджер "{manager}" не поддерживается'}

    def _pip_list(self) -> list[dict]:
        try:
            import importlib.metadata
            return [
                {'name': d.metadata['Name'], 'version': d.metadata['Version']}
                for d in importlib.metadata.distributions()
            ]
        except Exception:
            return []

    def _npm_list(self) -> list[dict]:
        result = self._run_tool_cmd("npm list --json --depth=0")
        if result.get('ok') and result.get('stdout'):
            try:
                import json
                data = json.loads(result['stdout'])
                deps = data.get('dependencies', {})
                return [{'name': k, 'version': v.get('version', '')} for k, v in deps.items()]
            except Exception:
                pass
        return []

    # ── Сервис-менеджмент ─────────────────────────────────────────────────────

    def list_services(self, name_filter: str = '') -> list[ServiceInfo]:
        """Список системных сервисов."""
        if self.is_windows:
            return self._list_services_windows(name_filter)
        elif self.is_linux:
            return self._list_services_linux(name_filter)
        elif self.is_macos:
            return self._list_services_macos(name_filter)
        return []

    def _list_services_windows(self, name_filter: str) -> list[ServiceInfo]:
        services = []
        try:
            import psutil
            for svc in psutil.win_service_iter():
                try:
                    info = svc.as_dict()
                    sname = info.get('name', '')
                    if name_filter and name_filter.lower() not in sname.lower():
                        continue
                    services.append(ServiceInfo(
                        name=sname,
                        status=info.get('status', 'unknown'),
                        pid=info.get('pid'),
                        start_type=info.get('start_type', ''),
                    ))
                except Exception:
                    continue
        except (ImportError, AttributeError):
            self._log("psutil.win_service_iter недоступен", level='warning')
        return services

    def _list_services_linux(self, name_filter: str) -> list[ServiceInfo]:
        services = []
        result = self._run_tool_cmd("systemctl list-units --type=service --no-pager --plain --no-legend")
        if not result.get('ok'):
            return services
        for line in (result.get('stdout', '') or '').splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            sname = parts[0].replace('.service', '')
            status = 'running' if parts[3] == 'running' else 'stopped'
            if name_filter and name_filter.lower() not in sname.lower():
                continue
            services.append(ServiceInfo(name=sname, status=status))
        return services

    def _list_services_macos(self, name_filter: str) -> list[ServiceInfo]:
        services = []
        result = self._run_tool_cmd("launchctl list")
        if not result.get('ok'):
            return services
        for line in (result.get('stdout', '') or '').splitlines()[1:]:
            parts = line.split('\t')
            if len(parts) < 3:
                continue
            pid_str, _status_code, label = parts[0], parts[1], parts[2]
            if name_filter and name_filter.lower() not in label.lower():
                continue
            pid = int(pid_str) if pid_str.strip() != '-' and pid_str.strip().isdigit() else None
            status = 'running' if pid is not None else 'stopped'
            services.append(ServiceInfo(name=label, status=status, pid=pid))
        return services

    # ── Сеть ──────────────────────────────────────────────────────────────────

    def list_network_interfaces(self) -> list[NetworkInterface]:
        """Информация обо всех сетевых интерфейсах."""
        interfaces = []
        try:
            import psutil
            addrs = psutil.net_if_addrs()
            stats = psutil.net_if_stats()
            for iface_name, addr_list in addrs.items():
                ips = []
                mac = ''
                for addr in addr_list:
                    if addr.family.name == 'AF_INET':
                        ips.append(addr.address)
                    elif addr.family.name == 'AF_INET6':
                        ips.append(addr.address)
                    elif addr.family.name in ('AF_LINK', 'AF_PACKET'):
                        mac = addr.address
                istat = stats.get(iface_name)
                interfaces.append(NetworkInterface(
                    name=iface_name,
                    ip_addresses=ips,
                    mac_address=mac,
                    is_up=istat.isup if istat else False,
                    speed_mbps=istat.speed if istat else 0,
                ))
        except ImportError:
            self._log("psutil недоступен — сеть ограничена", level='warning')
        return interfaces

    def get_open_ports(self) -> list[dict]:
        """Список открытых TCP-портов (прослушивающих)."""
        ports = []
        try:
            import psutil
            for conn in psutil.net_connections(kind='tcp'):
                if conn.status == 'LISTEN':
                    laddr = conn.laddr
                    ports.append({
                        'port': getattr(laddr, 'port', None),
                        'address': getattr(laddr, 'ip', ''),
                        'pid': conn.pid,
                    })
        except (ImportError, PermissionError, OSError):
            pass
        return ports

    def get_network_usage(self) -> dict:
        """Текущая статистика сетевого трафика."""
        try:
            import psutil
            counters = psutil.net_io_counters()
            return {
                'bytes_sent': counters.bytes_sent,
                'bytes_recv': counters.bytes_recv,
                'packets_sent': counters.packets_sent,
                'packets_recv': counters.packets_recv,
                'errors_in': counters.errin,
                'errors_out': counters.errout,
            }
        except ImportError:
            return {}

    # ── Устройства ────────────────────────────────────────────────────────────

    def list_devices(self) -> list[DeviceInfo]:
        """
        Перечисляет подключённые устройства.
        Windows: WMI (USB), Linux: /sys/bus/usb, macOS: system_profiler.
        """
        if self.is_windows:
            return self._list_devices_windows()
        elif self.is_linux:
            return self._list_devices_linux()
        elif self.is_macos:
            return self._list_devices_macos()
        return []

    def _list_devices_windows(self) -> list[DeviceInfo]:
        devices = []
        try:
            import subprocess
            # Используем PowerShell (безопасно — только чтение)
            cmd = (
                'powershell -NoProfile -Command '
                '"Get-PnpDevice -Status OK | '
                'Select-Object -First 50 InstanceId, FriendlyName, Class | '
                'ConvertTo-Json"'
            )
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, shell=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                import json
                data = json.loads(proc.stdout)
                if isinstance(data, dict):
                    data = [data]
                for item in data:
                    dev_id = item.get('InstanceId', '')
                    name = item.get('FriendlyName', '') or dev_id
                    cls = (item.get('Class', '') or '').lower()
                    dtype = 'usb' if 'usb' in dev_id.lower() else cls or 'other'
                    devices.append(DeviceInfo(
                        device_id=dev_id,
                        name=name,
                        device_type=dtype,
                    ))
        except Exception as e:
            self._log(f"Windows device enumeration: {e}", level='warning')
        return devices

    def _list_devices_linux(self) -> list[DeviceInfo]:
        devices = []
        usb_path = '/sys/bus/usb/devices'
        if not os.path.isdir(usb_path):
            return devices
        try:
            for entry in os.listdir(usb_path):
                dev_dir = os.path.join(usb_path, entry)
                product_file = os.path.join(dev_dir, 'product')
                vendor_file = os.path.join(dev_dir, 'manufacturer')
                name = ''
                vendor = ''
                if os.path.isfile(product_file):
                    try:
                        with open(product_file, 'r', encoding='utf-8', errors='ignore') as f:
                            name = f.read().strip()
                    except (PermissionError, OSError):
                        pass
                if os.path.isfile(vendor_file):
                    try:
                        with open(vendor_file, 'r', encoding='utf-8', errors='ignore') as f:
                            vendor = f.read().strip()
                    except (PermissionError, OSError):
                        pass
                if name:
                    devices.append(DeviceInfo(
                        device_id=entry,
                        name=name,
                        device_type='usb',
                        vendor=vendor,
                    ))
        except OSError:
            pass
        return devices

    def _list_devices_macos(self) -> list[DeviceInfo]:
        devices = []
        try:
            import subprocess
            proc = subprocess.run(
                ['system_profiler', 'SPUSBDataType', '-json'],
                capture_output=True, text=True, timeout=10,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                import json
                data = json.loads(proc.stdout)
                usb_items = data.get('SPUSBDataType', [])
                for bus in usb_items:
                    for item in bus.get('_items', []):
                        devices.append(DeviceInfo(
                            device_id=item.get('serial_num', item.get('_name', '')),
                            name=item.get('_name', ''),
                            device_type='usb',
                            vendor=item.get('manufacturer', ''),
                        ))
        except Exception as e:
            self._log(f"macOS device enumeration: {e}", level='warning')
        return devices

    # ── Привилегии и права ────────────────────────────────────────────────────

    def check_privileges(self) -> dict:
        """Проверяет привилегии текущего процесса."""
        info: dict[str, Any] = {
            'username': self.get_os_info().username,
            'is_admin': False,
            'platform': self.get_os_info().platform.value,
        }
        if self.is_windows:
            try:
                import ctypes
                info['is_admin'] = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                pass
        else:
            try:
                info['is_admin'] = os.geteuid() == 0  # type: ignore[attr-defined]
                info['uid'] = os.getuid()              # type: ignore[attr-defined]
                info['gid'] = os.getgid()              # type: ignore[attr-defined]
                info['groups'] = list(os.getgroups())   # type: ignore[attr-defined]
            except AttributeError:
                pass
        return info

    def get_file_permissions(self, path: str) -> dict | None:
        """Получает права доступа к файлу."""
        if not os.path.exists(path):
            return None
        stat = os.stat(path)
        result: dict[str, Any] = {
            'path': os.path.abspath(path),
            'mode': oct(stat.st_mode),
            'readable': os.access(path, os.R_OK),
            'writable': os.access(path, os.W_OK),
            'executable': os.access(path, os.X_OK),
        }
        if not self.is_windows:
            result['uid'] = stat.st_uid
            result['gid'] = stat.st_gid
        return result

    # ── Переменные окружения (безопасный доступ) ──────────────────────────────

    # Секретные переменные — никогда не отдавать значения
    _SECRET_PATTERNS = frozenset({
        'KEY', 'TOKEN', 'SECRET', 'PASSWORD', 'PASS', 'CREDENTIAL',
        'AUTH', 'PRIVATE', 'CERT',
    })

    def list_env_vars(self, include_values: bool = False) -> list[dict]:
        """
        Список переменных окружения.
        Значения секретных переменных маскируются.
        """
        result = []
        for key, value in sorted(os.environ.items()):
            is_secret = any(pat in key.upper() for pat in self._SECRET_PATTERNS)
            entry: dict[str, Any] = {'name': key, 'is_secret': is_secret}
            if include_values:
                entry['value'] = '***' if is_secret else value
            result.append(entry)
        return result

    def get_env(self, name: str, default: str = '') -> str:
        """Безопасное чтение переменной. Секретные возвращаются как ***."""
        is_secret = any(pat in name.upper() for pat in self._SECRET_PATTERNS)
        if is_secret:
            return '***' if os.environ.get(name) else default
        return os.environ.get(name, default)

    # ── Полный снимок состояния ОС ────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Полный снимок состояния ОС для Cognitive Core / Decision Making."""
        info = self.get_os_info()
        disks = self.disk_usage()
        net_ifs = self.list_network_interfaces()
        privs = self.check_privileges()

        mem: dict = {}
        cpu_pct: float = 0.0
        try:
            import psutil
            vm = psutil.virtual_memory()
            mem = {
                'total_gb': round(vm.total / (1024 ** 3), 2),
                'available_gb': round(vm.available / (1024 ** 3), 2),
                'percent': vm.percent,
            }
            cpu_pct = psutil.cpu_percent(interval=0.1)
        except ImportError:
            pass

        return {
            'os': info.to_dict(),
            'cpu_percent': cpu_pct,
            'memory': mem,
            'disks': [
                {'mount': d.mount_point, 'free_gb': d.free_gb, 'percent_used': d.percent_used}
                for d in disks
            ],
            'network_interfaces': len(net_ifs),
            'network_up': [n.name for n in net_ifs if n.is_up],
            'privileges': privs,
            'timestamp': time.time(),
        }

    # ── Вспомогательные ───────────────────────────────────────────────────────

    def _run_tool_cmd(self, command: str) -> dict:
        """Выполняет команду через Tool Layer / TerminalTool (безопасно)."""
        if self.tool_layer:
            try:
                if hasattr(self.tool_layer, 'use'):
                    result = self.tool_layer.use('terminal', command=command)
                    return {'ok': True, 'stdout': str(result)}
                if isinstance(self.tool_layer, dict) and 'terminal' in self.tool_layer:
                    result = self.tool_layer['terminal'](command=command)
                    return {'ok': True, 'stdout': str(result)}
            except Exception as e:
                return {'ok': False, 'error': str(e)}

        # Fallback: прямой subprocess (с ограничениями)
        import subprocess
        try:
            proc = subprocess.run(
                command.split(),
                capture_output=True, text=True, timeout=30,
                check=False,
            )
            return {
                'ok': proc.returncode == 0,
                'stdout': proc.stdout,
                'stderr': proc.stderr,
                'returncode': proc.returncode,
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            try:
                fn = getattr(self.monitoring, level, None) or getattr(self.monitoring, 'log', None)
                if fn is not None:
                    fn(message, source='os_layer')  # type: ignore[misc]
            except Exception:
                pass
