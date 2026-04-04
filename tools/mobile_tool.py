"""
MobileTool — управление Android/iOS устройствами и эмуляторами.

Бесплатные движки:
  1. ADB (Android Debug Bridge) — БЕСПЛАТНО
       Установка: https://developer.android.com/tools/releases/platform-tools
       Windows: winget install Google.PlatformTools
       Умеет: список устройств, установка APK, скриншот, shell, файлы, logcat, tap/swipe.
  2. Appium — БЕСПЛАТНО (npm install -g appium)
       UI-автоматизация Android и iOS.
  3. xcrun / libimobiledevice — iOS на macOS (бесплатно).

Переменные окружения:
  ADB_PATH=C:\\Users\\user\\AppData\\Local\\Android\\Sdk\\platform-tools\\adb.exe
  APPIUM_HOST=http://localhost:4723
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request

from tools.tool_layer import BaseTool


def _find_adb(custom: str | None = None) -> str | None:
    if custom and os.path.exists(custom):
        return custom
    for cmd in ('adb', 'adb.exe'):
        found = shutil.which(cmd)
        if found:
            return found
    home = os.path.expanduser('~')
    candidates = [
        os.path.join(home, 'AppData', 'Local', 'Android', 'Sdk', 'platform-tools', 'adb.exe'),
        os.path.join(home, 'Android', 'Sdk', 'platform-tools', 'adb'),
        '/usr/local/bin/adb',
        '/opt/android-sdk/platform-tools/adb',
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _no_adb() -> dict:
    return {
        'ok': False,
        'error': 'ADB не найден.',
        'install': 'https://developer.android.com/tools/releases/platform-tools',
        'windows': 'winget install Google.PlatformTools',
        'env_hint': 'Или укажи ADB_PATH в .env',
    }


def _run_adb(adb: str | None, args: list, timeout: int = 30) -> dict:
    if not adb:
        return _no_adb()
    try:
        r = subprocess.run(
            [adb] + args,
            capture_output=True, text=True, errors='replace', timeout=timeout, check=False,
        )
        return {
            'ok': r.returncode == 0,
            'stdout': r.stdout.strip(),
            'stderr': r.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {'ok': False, 'error': f'Таймаут {timeout}с'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


class MobileTool(BaseTool):
    name = 'mobile'
    description = (
        'Управление Android/iOS устройствами и эмуляторами: '
        'список устройств, установка APK, запуск приложений, скриншот, '
        'shell-команды, файлы, logcat, tap/swipe/text, Appium UI-автоматизация.'
    )

    def __init__(self, adb_path: str | None = None, appium_host: str | None = None):
        super().__init__(name=self.name, description=self.description)
        path = adb_path or os.environ.get('ADB_PATH')
        self._adb = _find_adb(path)
        self._appium = appium_host or os.environ.get('APPIUM_HOST', 'http://localhost:4723')

    def _args(self, device_id: str | None) -> list:
        return ['-s', device_id] if device_id else []

    # ─── ADB методы ──────────────────────────────────────────────────────────

    def list_devices(self) -> dict:
        """Список подключённых Android устройств и эмуляторов."""
        r = _run_adb(self._adb, ['devices', '-l'])
        if r['ok']:
            lines = [ln for ln in r['stdout'].split('\n')[1:] if ln.strip() and 'offline' not in ln]
            r['devices'] = lines
            r['count'] = len(lines)
        return r

    def install_apk(self, apk_path: str, device_id: str | None = None) -> dict:
        """Установить APK на устройство."""
        return _run_adb(self._adb, self._args(device_id) + ['install', '-r', apk_path], timeout=120)

    def uninstall(self, package: str, device_id: str | None = None) -> dict:
        """Удалить приложение по имени пакета (например, com.example.app)."""
        return _run_adb(self._adb, self._args(device_id) + ['uninstall', package], timeout=60)

    def launch_app(self, package: str, device_id: str | None = None) -> dict:
        """Запустить приложение через monkey launcher."""
        return _run_adb(
            self._adb,
            self._args(device_id) + [
                'shell', 'monkey', '-p', package,
                '-c', 'android.intent.category.LAUNCHER', '1',
            ],
        )

    def stop_app(self, package: str, device_id: str | None = None) -> dict:
        """Остановить приложение (force-stop)."""
        return _run_adb(self._adb, self._args(device_id) + ['shell', 'am', 'force-stop', package])

    def screenshot(self, dst: str, device_id: str | None = None) -> dict:
        """Скриншот экрана устройства → сохранить локально в dst."""
        tmp = '/sdcard/screenshot_agent.png'
        r1 = _run_adb(self._adb, self._args(device_id) + ['shell', 'screencap', '-p', tmp])
        if not r1['ok']:
            return r1
        return _run_adb(self._adb, self._args(device_id) + ['pull', tmp, dst])

    def push_file(self, local: str, remote: str, device_id: str | None = None) -> dict:
        """Скопировать файл с компьютера на устройство."""
        return _run_adb(self._adb, self._args(device_id) + ['push', local, remote], timeout=120)

    def pull_file(self, remote: str, local: str, device_id: str | None = None) -> dict:
        """Скачать файл с устройства на компьютер."""
        return _run_adb(self._adb, self._args(device_id) + ['pull', remote, local], timeout=120)

    def shell(self, command: str, device_id: str | None = None) -> dict:
        """Выполнить shell-команду на устройстве. Пример: shell('ls /sdcard')."""
        return _run_adb(
            self._adb,
            self._args(device_id) + ['shell'] + command.split(),
        )

    def logcat(self, lines: int = 100,
               filter_tag: str | None = None,
               device_id: str | None = None) -> dict:
        """Последние строки logcat (лог приложений)."""
        args = self._args(device_id) + ['logcat', '-d', '-t', str(lines)]
        if filter_tag:
            args += [f'{filter_tag}:V', '*:S']
        return _run_adb(self._adb, args, timeout=15)

    def list_packages(self, filter_str: str | None = None,
                      device_id: str | None = None) -> dict:
        """Список установленных пакетов."""
        args = self._args(device_id) + ['shell', 'pm', 'list', 'packages']
        if filter_str:
            args.append(filter_str)
        r = _run_adb(self._adb, args)
        if r['ok']:
            pkgs = [p.replace('package:', '') for p in r['stdout'].split('\n') if p.strip()]
            r['packages'] = pkgs
            r['count'] = len(pkgs)
        return r

    def input_text(self, text: str, device_id: str | None = None) -> dict:
        """Ввести текст (эмуляция клавиатуры). Пробелы заменяются на %s."""
        safe = text.replace(' ', '%s')
        return _run_adb(self._adb, self._args(device_id) + ['shell', 'input', 'text', safe])

    def tap(self, x: int, y: int, device_id: str | None = None) -> dict:
        """Нажать на экран по координатам."""
        return _run_adb(self._adb, self._args(device_id) + ['shell', 'input', 'tap', str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int,
              duration_ms: int = 300, device_id: str | None = None) -> dict:
        """Свайп на экране от (x1,y1) до (x2,y2)."""
        return _run_adb(
            self._adb,
            self._args(device_id) + [
                'shell', 'input', 'swipe',
                str(x1), str(y1), str(x2), str(y2), str(duration_ms),
            ],
        )

    def key_event(self, keycode: int, device_id: str | None = None) -> dict:
        """
        Нажать кнопку. Коды: 3=HOME, 4=BACK, 26=POWER, 24=VOL+, 25=VOL-.
        Полный список: https://developer.android.com/reference/android/view/KeyEvent
        """
        return _run_adb(self._adb, self._args(device_id) + ['shell', 'input', 'keyevent', str(keycode)])

    def get_device_info(self, device_id: str | None = None) -> dict:
        """Информация об устройстве (модель, Android версия, разрешение)."""
        results = {}
        for prop, cmd in [
            ('model',   'getprop ro.product.model'),
            ('brand',   'getprop ro.product.brand'),
            ('android', 'getprop ro.build.version.release'),
            ('sdk',     'getprop ro.build.version.sdk'),
        ]:
            r = _run_adb(self._adb, self._args(device_id) + ['shell'] + cmd.split())
            results[prop] = r.get('stdout', '').strip() if r['ok'] else 'N/A'
        return {'ok': True, **results}

    # ─── Appium UI-автоматизация ─────────────────────────────────────────────

    def appium_session(self, desired_caps: dict) -> dict:
        """
        Создать Appium-сессию для UI-автоматизации.
        desired_caps — словарь capabilities (platformName, deviceName, app, и т.д.).
        Appium должен быть запущен: appium --port 4723
        Установка Appium: npm install -g appium  (бесплатно)
        """
        url = f'{self._appium}/session'
        body = json.dumps({'desiredCapabilities': desired_caps}).encode()
        req = urllib.request.Request(
            url, data=body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode())
                return {'ok': True, 'session_id': data.get('sessionId'), **data}
        except Exception as e:
            msg = str(e)
            if 'Connection refused' in msg:
                return {
                    'ok': False,
                    'error': f'Appium не запущен на {self._appium}',
                    'start': 'Запусти: appium --port 4723',
                    'install': 'npm install -g appium (бесплатно)',
                }
            return {'ok': False, 'error': msg}

    # ─── iOS (только macOS) ──────────────────────────────────────────────────

    def ios_list_devices(self) -> dict:
        """Список iOS устройств (только macOS с установленным Xcode)."""
        if not shutil.which('xcrun'):
            return {
                'ok': False,
                'error': 'xcrun не найден. iOS автоматизация доступна только на macOS.',
                'note': 'Установи Xcode Command Line Tools: xcode-select --install',
            }
        try:
            r = subprocess.run(
                ['xcrun', 'xctrace', 'list', 'devices'],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return {'ok': r.returncode == 0, 'output': r.stdout}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── run() dispatcher ────────────────────────────────────────────────────

    def run(self, *args, action: str = 'list_devices', **params) -> dict:
        """
        action:
          list_devices | install_apk | uninstall | launch_app | stop_app |
          screenshot | push_file | pull_file | shell | logcat |
          list_packages | input_text | tap | swipe | key_event | get_device_info |
          appium_session | ios_list_devices
        """
        actions = {
            'list_devices':   self.list_devices,
            'install_apk':    self.install_apk,
            'uninstall':      self.uninstall,
            'launch_app':     self.launch_app,
            'stop_app':       self.stop_app,
            'screenshot':     self.screenshot,
            'push_file':      self.push_file,
            'pull_file':      self.pull_file,
            'shell':          self.shell,
            'logcat':         self.logcat,
            'list_packages':  self.list_packages,
            'input_text':     self.input_text,
            'tap':            self.tap,
            'swipe':          self.swipe,
            'key_event':      self.key_event,
            'get_device_info': self.get_device_info,
            'appium_session': self.appium_session,
            'ios_list_devices': self.ios_list_devices,
        }
        fn = actions.get(action)
        if not fn:
            return {'ok': False, 'error': f'Неизвестный action: {action}. Доступные: {list(actions)}'}
        try:
            return fn(**params) or {'ok': True}
        except TypeError as e:
            return {'ok': False, 'error': f'Неверные параметры: {e}'}
