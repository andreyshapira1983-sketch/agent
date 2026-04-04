# ActionDispatcher — мост между планом LLM и реальными инструментами — Слой 8.1
# Архитектура автономного AI-агента
#
# Извлекает исполняемые блоки из текста плана и запускает их через ToolLayer.
# Используется AutonomousLoop._act() сразу после получения плана от AgentSystem.
#
# Поддерживаемые форматы в тексте плана:
#
#   ```bash               → TerminalTool
#   command here
#   ```
#
#   ```python             → PythonRuntimeTool
#   code here
#   ```
#
#   SEARCH: query         → SearchTool
#   READ: /path/to/file   → FileSystemTool(action='read')
#   WRITE: /path/to/file  → FileSystemTool(action='write')
#   CONTENT: text here
#
# Безопасность: все вызовы идут через tool_layer.use(), который содержит
#   CommandValidator, PathValidator, CodeValidator с whitelist-подходом.
#   При отсутствии tool_layer bash-блоки выполняются через ExecutionSystem.

import os
import re
import subprocess
import time
import ast
import json
import hashlib
import threading

from execution.task_router import TaskRouter as _TaskRouter, TaskIntent as _TaskIntent, TaskStatus as _TaskStatus
from safety.hardening import ContentSanitizer as _ContentSanitizer, RateLimiter as _RateLimiter, InstructionClassifier as _InstructionClassifier
from evaluation.action_contracts import contract_for_action_type as _contract_for_action_type, verify_contract as _verify_contract

_TASK_ROUTER = _TaskRouter()


# Регулярные выражения для извлечения блоков действий
_RE_BASH   = re.compile(r'```(?:bash|shell|sh)\n(.*?)```', re.DOTALL)
_RE_PYTHON = re.compile(r'```python\n(.*?)```', re.DOTALL)
_RE_SEARCH = re.compile(r'^SEARCH:\s*(.+)$', re.MULTILINE)
_RE_READ   = re.compile(r'^READ:\s*(.+)$', re.MULTILINE)
_RE_WRITE  = re.compile(r'^WRITE:\s*(.+)\nCONTENT:\s*([\s\S]+?)(?=\n[A-Z]+:|$)',
                        re.MULTILINE)
# Дополнительный формат: WRITE: file.py  затем ```python\nкод\n```
_RE_WRITE_CODE = re.compile(
    r'^WRITE:\s*(.+)\n```(?:python|js|javascript|ts|typescript|bash|sh|bat|ps1)?\n'
    r'([\s\S]+?)```',
    re.MULTILINE,
)

# Расширения файлов с исходным кодом — для них проверяем заглушки
_CODE_EXTENSIONS = {'.py', '.js', '.ts', '.jsx', '.tsx', '.sh', '.bat', '.ps1', '.rb', '.go'}
# Фразы-маркеры заглушек
_STUB_PHRASES = (
    'код выше', 'code above', 'см. выше', 'see above',
    'вставить код', 'написать код', 'добавить код',
    'здесь код', 'код здесь', 'реализация выше',
    'заглушка', 'placeholder', 'todo: implement', '# todo',
)
# Фразы-заглушки универсальные (для всех типов файлов, не только кода)
_UNIVERSAL_STUB_PHRASES = (
    'результат выполнения python-кода выше',
    'результат выполнения кода выше',
    'результат python-кода выше',
    'вывод python-кода выше',
    'см. код выше',
)

# ── Резервные паттерны для русскоязычных планов LLM ───────────────────────────
# Распознаёт строки вида "Шаг 1: Найди информацию о X" → SEARCH: X
# и строки вида "1. Запусти скрипт Y"                  → bash: Y
_RE_RU_SEARCH = re.compile(
    r'^(?:[-*•]|\d+[.)]\s*)?\s*'
    r'(?:найди|поищи|исследуй|загрузи данные о|получи информацию|узнай|поиск)\s+(.+)',
    re.MULTILINE | re.IGNORECASE,
)
_RE_RU_READ = re.compile(
    r'^(?:[-*•]|\d+[.)]\s*)?\s*'
    r'(?:прочитай файл|открой файл|читай файл|загрузи файл)\s+(.+)',
    re.MULTILINE | re.IGNORECASE,
)
_RE_RU_BASH = re.compile(
    r'^(?:[-*•]|\d+[.)]\s*)?\s*'
    r'(?:запусти|выполни команду|выполни)\s+`([^`]+)`',
    re.MULTILINE | re.IGNORECASE,
)
_RE_RU_PYTHON = re.compile(
    r'^(?:[-*•]|\d+[.)]\s*)?\s*'
    r'(?:выполни код|запусти код|запусти скрипт)\s+`([^`]+)`',
    re.MULTILINE | re.IGNORECASE,
)

# Максимальный размер кода/команды для безопасности
_MAX_CODE_LEN = 4000
_MAX_CMD_LEN  = 500
_DEFAULT_OUTPUT_DIR = 'outputs'

# Валидный путь к файлу: допустимы буквы (в т.ч. кириллица), цифры, / \ . _ - пробел
_RE_VALID_PATH = re.compile(r'^[\w\u0400-\u04FF ./_\\-]+$')

def _sanitize_file_path(raw: str) -> str:
    """
    Очищает строку, которую LLM выдал как путь к файлу.
    - Обрезает всё после '(' (описание в скобках)
    - Проверяет что оставшийся путь выглядит как реальный файл
    Возвращает очищенный путь или '' если путь невалиден.
    """
    # Обрезаем описание в скобках: "outputs/file.txt (описание)" → "outputs/file.txt"
    path = raw.split('(')[0].strip()
    # Не больше 200 символов
    if len(path) > 200:
        return ''
    # Должен содержать хотя бы одну букву/цифру и быть похожим на путь
    if not path or not _RE_VALID_PATH.match(path):
        return ''
    # Имя файла (последний компонент) должно содержать точку — признак расширения
    basename = path.replace('\\', '/').rstrip('/').split('/')[-1]
    if '.' not in basename:
        return ''
    return path

_PS_RISKY_TOKENS = (
    ';', '|', '&', '`', '$(',
    'invoke-expression', 'iex',
    'start-process', 'stop-process',
    'remove-item', 'rename-item', 'move-item',
    'set-content', 'add-content', 'out-file',
    'new-item', 'copy-item',
    'set-itemproperty', 'set-service',
    'sc.exe', 'reg add', 'schtasks',
)


class ActionDispatcher:
    """
    Мост между текстовым планом LLM и реальным исполнением через инструменты.

    Используется в AutonomousLoop._act() ПОСЛЕ вызова agent_system.handle().
    Сканирует текст плана на блоки действий и исполняет каждый.

    Возвращает:
        {
            'actions_found':    int,   # сколько блоков найдено
            'actions_executed': int,   # сколько успешно запущено
            'results':          list,  # детали по каждому действию
            'success':          bool,  # все ли успешны
            'summary':          str,   # краткая сводка для контекста LLM
        }
    """

    def __init__(self, tool_layer=None, execution_system=None, monitoring=None, llm=None,
                 acquisition_pipeline=None, module_builder=None, security=None):
        self.tool_layer       = tool_layer
        self.execution_system = execution_system
        self.monitoring       = monitoring
        self._security        = security
        self._llm             = llm   # для retry при STUB_REJECTED
        self.acquisition_pipeline = acquisition_pipeline  # сохраняем URL из поиска в knowledge
        self.module_builder   = module_builder  # ModuleBuilder — создание новых .py модулей
        self._dedup_window_sec = self._read_int_env('ACTION_DEDUP_WINDOW_SEC', 1800)
        self._recent_action_seen: dict[str, float] = {}
        self._dedup_lock = threading.Lock()
        # Текущая цель: скоупит dedup и relevance-check к конкретному goal
        self._current_goal: str = ''
        self._reject_memory_window_sec = self._read_int_env('WRITE_REJECT_MEMORY_SEC', 7 * 24 * 3600)
        self._reject_memory_path = os.path.join('.agent_memory', 'write_rejections.json')
        self._reject_memory: list[dict] = self._load_reject_memory()
        # Гарантируем существование папки outputs/ при старте
        os.makedirs(_DEFAULT_OUTPUT_DIR, exist_ok=True)
        # Проверяем наличие WSL один раз при старте
        self._wsl_available = self._check_wsl()
        # ── RateLimiter: hard limits на действия ──────────────────────────────
        self._rate_limiter = _RateLimiter()

    # ── Публичный API ─────────────────────────────────────────────────────────

    def reset_cycle_limits(self):
        """Сбрасывает per-cycle счётчики RateLimiter. Вызывается в начале каждого цикла."""
        self._rate_limiter.reset_cycle()

    def check_kill_switch(self) -> tuple[bool, str]:
        """Проверяет kill switch. Возвращает (ok, reason)."""
        return self._rate_limiter.check_kill_switch()

    def dispatch(self, text: str, goal: str = '',
                 fail_closed_on_semantic_reject: bool = True) -> dict:
        """
        Извлекает все исполняемые блоки из текста и выполняет их.

        Args:
            text — текст плана / ответа LLM (может содержать несколько блоков)

        Returns:
            Сводный результат исполнения.
        """
        if not text:
            return self._empty_result()

        # ── Kill switch: немедленная остановка ─────────────────────────────
        kill_ok, kill_reason = self._rate_limiter.check_kill_switch()
        if not kill_ok:
            self._log(f'[dispatch] {kill_reason}')
            return self._empty_result()

        # ── InstructionClassifier: блокируем опасные инструкции ─────────
        intent, intent_detail = _InstructionClassifier.classify(text[:2000])
        if intent == _InstructionClassifier.Intent.BLOCKED:
            self._log(f'[dispatch] BLOCKED instruction: {intent_detail}')
            return {
                'actions_found': 0, 'actions_executed': 0,
                'results': [{'type': 'blocked', 'input': text[:200],
                             'output': '', 'success': False,
                             'error': f'INSTRUCTION_BLOCKED: {intent_detail}'}],
                'success': False,
                'summary': f'Инструкция заблокирована классификатором: {intent_detail}',
                'semantic_rejected': False, 'semantic_rejected_count': 0,
            }

        # Сохраняем goal для dedup-scope и relevance check
        self._current_goal = str(goal or '').strip()

        actions = self._extract_actions(text)
        if not actions:
            return self._empty_result()

        actions = self._deduplicate_actions(actions)
        if not actions:
            return {
                'actions_found': 0,
                'actions_executed': 0,
                'results': [],
                'success': True,
                'summary': 'Все действия были дубликатами и пропущены.',
                'semantic_rejected': False,
                'semantic_rejected_count': 0,
            }
        total_actions_found = len(actions)
        blocked_results: list[dict] = []

        # ── Pre-dispatch: router-check по цели ────────────────────────────────
        # Если цель — заголовок раздела или личный tool без web-search прав,
        # не даём search-action выполниться вместо реального tool.
        if goal and goal.strip():
            _route = _TASK_ROUTER.route(goal)
            # 1. NON_ACTIONABLE (заголовок) → пропускаем без ошибки
            if _route.intent in (_TaskIntent.HEADING, _TaskIntent.EMPTY):
                return {
                    'actions_found': 0,
                    'actions_executed': 0,
                    'results': [],
                    'success': True,
                    'status': 'non_actionable',
                    'summary': f'NON_ACTIONABLE: {_route.reason}',
                    'semantic_rejected': False,
                    'semantic_rejected_count': 0,
                }
            # 2. BLOCKED → возвращаем причину без выполнения
            if _route.status == _TaskStatus.BLOCKED:
                return {
                    'actions_found': len(actions),
                    'actions_executed': 0,
                    'results': [{
                        'type': 'blocked',
                        'input': goal[:200],
                        'output': _route.reason,
                        'success': False,
                        'error': f'BLOCKED: {_route.reason}',
                        'status': 'blocked',
                        'missing_args': [a.description for a in _route.missing_args],
                        'latency_ms': 0,
                    }],
                    'success': False,
                    'status': 'blocked',
                    'summary': f'BLOCKED: {_route.reason}',
                    'semantic_rejected': False,
                    'semantic_rejected_count': 0,
                }
            # 3. Запрет web-search для личных tool-интентов
            if _route.block_web_search:
                non_search = [a for a in actions if a.get('type') != 'search']
                blocked_searches = [a for a in actions if a.get('type') == 'search']
                if blocked_searches:
                    for bs in blocked_searches:
                        blocked_results.append({
                            'type': 'search',
                            'input': str(bs.get('input', ''))[:200],
                            'output': '',
                            'success': False,
                            'error': (
                                f'BLOCKED_WEB_SEARCH: goal intent={_route.intent.value}, '
                                f'web-search запрещён для этого класса задач. '
                                f'Используй tool={_route.tool_name}.'
                            ),
                            'latency_ms': 0,
                        })
                    actions = non_search
                    if not actions:
                        return {
                            'actions_found': total_actions_found,
                            'actions_executed': 0,
                            'results': blocked_results,
                            'success': False,
                            'status': 'blocked',
                            'summary': (
                                f'BLOCKED: web-search запрещён для intent={_route.intent.value}. '
                                f'Задача требует tool={_route.tool_name}.'
                            ),
                            'semantic_rejected': False,
                            'semantic_rejected_count': 0,
                        }

        # Семантический gate: действие должно быть релевантно цели,
        # а не только формально проходить whitelist валидаторов.
        goal_text = str(goal or '')
        if goal_text.strip():
            filtered_actions: list[dict] = []
            for action in actions:
                ok, reason = self._semantic_action_gate(action, goal_text)
                if ok:
                    filtered_actions.append(action)
                else:
                    blocked_results.append({
                        'type': action.get('type', 'unknown'),
                        'input': str(action.get('input', ''))[:200],
                        'output': '',
                        'success': False,
                        'error': f'SEMANTIC_GATE_REJECTED: {reason}',
                        'latency_ms': 0,
                    })
            actions = filtered_actions
            # FAIL-CLOSED только если отклонены ВСЕ действия.
            # Если остались валидные шаги — выполняем их, а отклонённые возвращаем в отчёте.
            if blocked_results and fail_closed_on_semantic_reject and not actions:
                # FAIL-CLOSED: если ВСЕ действия нерелевантны — не исполняем ничего,
                # чтобы не продолжать потенциально неверный план.
                skipped_results = []
                all_results = blocked_results + skipped_results
                return {
                    'actions_found': total_actions_found,
                    'actions_executed': 0,
                    'results': all_results,
                    'success': False,
                    'summary': (
                        'FAIL-CLOSED: обнаружены нерелевантные действия. '
                        'Выполнение остановлено, требуется реплан.'
                    ),
                    'semantic_rejected': True,
                    'semantic_rejected_count': len(blocked_results),
                }
            if not actions:
                return {
                    'actions_found': len(blocked_results),
                    'actions_executed': 0,
                    'results': blocked_results,
                    'success': False,
                    'summary': 'Все действия отклонены semantic gate как нерелевантные цели.',
                    'semantic_rejected': True,
                    'semantic_rejected_count': len(blocked_results),
                }

        results = []
        _py_blocked = False  # флаг: предыдущий python-блок заблокирован/упал
        for action in actions:
            # ── Каскадная защита: если предыдущий python-блок упал,
            #    последующие python-блоки пропускаем (namespace неполный) ──
            if _py_blocked and action.get('type') == 'python':
                self._log('[python] SKIP: предыдущий python-блок не выполнен, namespace неполный')
                results.append({
                    'type': 'python',
                    'input': str(action.get('input', ''))[:200],
                    'output': '', 'success': False,
                    'error': 'Пропущен: предыдущий python-блок не выполнен (namespace неполный)',
                    'latency_ms': 0,
                })
                continue

            # ── RateLimiter: per-action hard limit ─────────────────────────
            rate_ok, rate_reason = self._rate_limiter.check_action()
            if not rate_ok:
                self._log(f'[dispatch] {rate_reason}')
                results.append({
                    'type': action.get('type', 'unknown'),
                    'input': str(action.get('input', ''))[:200],
                    'output': '', 'success': False,
                    'error': rate_reason, 'latency_ms': 0,
                })
                break  # прекращаем цикл: лимит достигнут

            t_start = time.time()
            try:
                r = self._execute_action(action)
            except (TypeError, ValueError, AttributeError, RuntimeError, KeyError) as exc:
                r = {
                    'type':    action.get('type', 'unknown'),
                    'input':   str(action.get('input', ''))[:200],
                    'output':  '',
                    'success': False,
                    'error':   str(exc),
                    'latency_ms': 0,
                }
            r['latency_ms'] = round((time.time() - t_start) * 1000)

            # ── Contract verification: локальная проверка результата (бесплатно) ──
            if r.get('success'):
                try:
                    _contract = _contract_for_action_type(
                        action_type=r.get('type', ''),
                        action_input=str(r.get('input', '')),
                        action_output=str(r.get('output', '')),
                        action_success=r.get('success', False),
                        action_stderr=str(r.get('stderr', '')),
                    )
                    if _contract:
                        _vr = _verify_contract(_contract)
                        r['contract_score'] = _vr.score
                        _atype = r.get('type', '')
                        if not _vr.passed and not _vr.partial:
                            if _atype == 'search':
                                # Пустой поиск — нормально, не ошибка
                                self._log("[search] пустой результат (contract info, не ошибка)")
                            else:
                                r['success'] = False
                                r['error'] = f"contract_failed: {', '.join(_vr.failed_checks)}"
                                self._log(f"[{_atype}] CONTRACT FAIL: {_vr.failed_checks}")
                        elif _vr.partial:
                            self._log(f"[{_atype}] CONTRACT PARTIAL ({_vr.score:.0%}): "
                                      f"pass={_vr.passed_checks}, fail={_vr.failed_checks}")
                except Exception:
                    pass  # контракт не должен ломать основной поток

            results.append(r)

            # Обновляем флаг каскадной защиты:
            # блокируем только при NameError (namespace сломан), не при FileNotFoundError/OSError
            if action.get('type') == 'python' and not r.get('success'):
                _err = str(r.get('error', ''))
                _namespace_broken = (
                    'NameError' in _err
                    or 'UnboundLocalError' in _err
                    or 'namespace' in _err.lower()
                )
                if _namespace_broken:
                    _py_blocked = True

            if r['success'] and r.get('note'):
                input_preview = (r.get('input') or '')[:60].replace('\n', ' ')
                self._log(f"[{r['type']}] INFO: {r['note'][:80]}"
                          + (f" | cmd: {input_preview}" if input_preview else ""))
            else:
                self._log(f"[{r['type']}] "
                          + ("OK" if r['success'] else f"FAIL: {r.get('error', '')[:150]}"))

        # Добавляем отклонённые semantic-gate действия в общий отчёт
        if goal_text.strip():
            for br in blocked_results:
                results.append(br)
                self._log(f"[{br['type']}] FAIL: {br.get('error', '')[:150]}")

        executed     = sum(1 for r in results if r['success'] is not None)
        ok_count     = sum(1 for r in results if r.get('success'))
        success      = (ok_count / len(results) >= 0.5) if results else True
        summary      = self._make_summary(results)

        return {
            'actions_found':    total_actions_found,
            'actions_executed': executed,
            'results':          results,
            'success':          success,
            'summary':          summary,
            'semantic_rejected': bool(blocked_results),
            'semantic_rejected_count': len(blocked_results),
        }

    @staticmethod
    def _read_int_env(name: str, default: int) -> int:
        raw = os.environ.get(name, '').strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return value if value > 0 else default

    def _action_signature(self, action: dict) -> str:
        t = str(action.get('type', '')).strip().lower()
        inp = str(action.get('input', '')).strip().lower()
        content = str(action.get('content', '')).strip()
        if len(content) > 400:
            content = content[:400]
        # Добавляем хэш текущей цели в сигнатуру: одинаковый поиск для разных задач — это не дубликат.
        goal_hash = hashlib.md5(self._current_goal[:200].encode('utf-8', errors='replace')).hexdigest()[:8]
        # Для write учитываем и путь, и часть контента; для остальных — тип+вход.
        if t == 'write':
            return f"{t}|{goal_hash}|{inp}|{content}"
        return f"{t}|{goal_hash}|{inp}"

    def _deduplicate_actions(self, actions: list[dict]) -> list[dict]:
        if not actions:
            return actions

        now = time.time()
        # Очистка старых ключей окна дедупликации
        stale_before = now - float(self._dedup_window_sec)
        with self._dedup_lock:
            self._recent_action_seen = {
                sig: ts for sig, ts in self._recent_action_seen.items()
                if ts >= stale_before
            }

            unique_in_batch: set[str] = set()
            filtered: list[dict] = []
            skipped_batch = 0
            skipped_recent = 0

            for action in actions:
                sig = self._action_signature(action)
                if sig in unique_in_batch:
                    skipped_batch += 1
                    continue
                last_ts = self._recent_action_seen.get(sig)
                if last_ts is not None and (now - last_ts) < self._dedup_window_sec:
                    skipped_recent += 1
                    continue
                unique_in_batch.add(sig)
                filtered.append(action)
                self._recent_action_seen[sig] = now

        if skipped_batch or skipped_recent:
            self._log(
                f"[dedup] Пропущено дубликатов: batch={skipped_batch}, recent={skipped_recent}, "
                f"осталось={len(filtered)}"
            )

        return filtered

    @staticmethod
    def _goal_keywords(goal: str) -> set[str]:
        """Извлекает ключевые слова цели для оценки релевантности действий."""
        stop = {
            'и', 'в', 'на', 'по', 'с', 'для', 'или', 'что', 'как', 'это', 'эти',
            'the', 'and', 'for', 'with', 'from', 'that', 'this',
            'сделай', 'нужно', 'надо', 'задача', 'цель',
        }
        words = re.findall(r'[A-Za-zА-Яа-яЁё0-9_\-]{3,}', (goal or '').lower())
        return {w for w in words if w not in stop and len(w) >= 4}

    @staticmethod
    def _infer_domain(text: str) -> str:
        """Грубая доменная классификация: code/system/jobs/portfolio/research/unknown."""
        t = (text or '').lower()
        if any(k in t for k in ('python', 'код', 'script', 'git', 'pytest', 'debug', 'bug', 'repo')):
            return 'code'
        if any(k in t for k in ('cpu', 'ram', 'disk', 'процесс', 'service', 'монитор', 'система')):
            return 'system'
        if any(k in t for k in ('vacanc', 'upwork', 'job', 'ваканс', 'заказ', 'cover letter')):
            return 'jobs'
        if any(k in t for k in ('portfolio', 'портфолио', 'project', 'проект')):
            return 'portfolio'
        if any(k in t for k in ('исслед', 'research', 'search', 'источник', 'article', 'документац')):
            return 'research'
        return 'unknown'

    def _semantic_action_gate(self, action: dict, goal: str) -> tuple[bool, str]:
        """Проверяет релевантность действия цели (поверх whitelist-безопасности)."""
        goal_kw = self._goal_keywords(goal)
        if not goal_kw:
            return True, 'goal has no keywords'

        a_type = str(action.get('type', '')).lower()
        a_input = str(action.get('input', '')).lower()
        a_content = str(action.get('content', '')).lower()[:300]
        hay = f"{a_input} {a_content}"

        # Быстрый lexical overlap
        overlap = sum(1 for w in goal_kw if w in hay)
        if overlap >= 1:
            return True, 'keyword overlap'

        # Доменная совместимость (мягче, чем точный overlap)
        goal_domain = self._infer_domain(goal)
        action_domain = self._infer_domain(hay)

        # READ логов и outputs допускаем почти всегда для диагностики/аналитики
        if a_type == 'read' and any(k in a_input for k in ('log', 'task_log', 'outputs', 'result')):
            return True, 'read diagnostics artifact'

        # BASH/PYTHON — смягчаем доменную проверку, т.к. автоматизация часто кроссдоменна
        # (bash может запускать тесты кода, python может манипулировать системой)
        if a_type in ('bash', 'python'):
            # Пропускаем, если хотя бы одна из древних целей-действий в одном домене
            if goal_domain == 'unknown' or action_domain == 'unknown':
                return True, 'domain unknown, allow automation'
            # Если обе известны, но совпадают — OK
            if goal_domain == action_domain:
                return True, 'domain match'
            # Если не совпадают но оба доменные (code-system, code-research и т.д.) — 
            # всё равно пропускаем, т.к. автоматизация кроссдоменна
            return True, 'allow cross-domain automation'

        # SEARCH/WRITE строже: должны доменно совпадать
        if a_type in ('search', 'write'):
            if goal_domain != 'unknown' and action_domain != 'unknown' and goal_domain != action_domain:
                return False, f'domain mismatch goal={goal_domain}, action={action_domain}'

        return True, 'no strong mismatch'

    # ── Извлечение блоков действий ────────────────────────────────────────────

    def _extract_actions(self, text: str) -> list[dict]:
        """Находит все блоки действий в тексте плана."""
        actions = []

        # bash / shell блоки
        for m in _RE_BASH.finditer(text):
            cmd = m.group(1).strip()
            if cmd:
                actions.append({'type': 'bash', 'input': cmd[:_MAX_CMD_LEN]})

        # python блоки — вырезаем DSL-команды (SEARCH:/READ:/WRITE:) если LLM вставил их внутрь
        # \s* перед ключевым словом — ловим команды с отступом (внутри функций)
        _dsl_re = re.compile(r'^\s*(?:SEARCH|READ|WRITE|CONTENT):\s*.+$', re.MULTILINE)
        # Паттерн: var = SEARCH: "query" или var = SEARCH: query (без кавычек)
        _dsl_assign_re = re.compile(
            r'^\s*\w+\s*=\s*(SEARCH|READ):\s*(.+)$', re.MULTILINE
        )
        for m in _RE_PYTHON.finditer(text):
            raw_code = m.group(1).strip()
            if not raw_code:
                continue
            # Извлекаем встроенные DSL-команды как отдельные действия (с начала строки)
            for dsl_m in re.finditer(r'^\s*(SEARCH|READ):\s*(.+)$', raw_code, re.MULTILINE):
                dsl_type = dsl_m.group(1).lower()
                dsl_val  = dsl_m.group(2).strip().strip('"\'')
                if dsl_val:
                    actions.append({'type': dsl_type, 'input': dsl_val, 'source': 'extracted_from_python'})
            # Извлекаем DSL-команды из присваиваний: var = SEARCH: query
            for dsl_m in _dsl_assign_re.finditer(raw_code):
                dsl_type = dsl_m.group(1).lower()
                dsl_val  = dsl_m.group(2).strip().strip('"\'')
                if dsl_val:
                    actions.append({'type': dsl_type, 'input': dsl_val, 'source': 'extracted_from_assign'})
            # Удаляем DSL-строки из кода (и строки с присваиванием DSL)
            clean_code = _dsl_re.sub('', raw_code)
            clean_code = _dsl_assign_re.sub('', clean_code).strip()
            if clean_code:
                actions.append({'type': 'python', 'input': clean_code[:_MAX_CODE_LEN]})

        # SEARCH: запрос
        for m in _RE_SEARCH.finditer(text):
            query = m.group(1).strip()
            if query:
                actions.append({'type': 'search', 'input': query})

        # READ: путь
        for m in _RE_READ.finditer(text):
            path = _sanitize_file_path(m.group(1).strip())
            if path:
                actions.append({'type': 'read', 'input': path})

        # BUILD_MODULE: name | description  (создание нового Python-модуля)
        _RE_BUILD_MODULE = re.compile(
            r'^BUILD_MODULE:\s*([\w\-]+)\s*\|\s*(.+)$', re.MULTILINE
        )
        for m in _RE_BUILD_MODULE.finditer(text):
            name = m.group(1).strip()
            desc = m.group(2).strip()
            if name and desc:
                actions.append({'type': 'build_module', 'input': name, 'description': desc})

        # WRITE: путь / CONTENT: текст
        for m in _RE_WRITE.finditer(text):
            path    = _sanitize_file_path(m.group(1).strip())
            content = m.group(2).strip()
            if path and content:
                actions.append({'type': 'write', 'input': path, 'content': content})

        # WRITE: путь  + ```python\nкод\n``` — альтернативный формат LLM
        seen_write_paths = {a['input'] for a in actions if a['type'] == 'write'}
        for m in _RE_WRITE_CODE.finditer(text):
            path    = _sanitize_file_path(m.group(1).strip())
            content = m.group(2).strip()
            if path and content and path not in seen_write_paths:
                actions.append({'type': 'write', 'input': path, 'content': content})
                seen_write_paths.add(path)

        # ── Резервные паттерны: русскоязычный план без маркеров ──────────────
        # Срабатывают ТОЛЬКО если основные паттерны ничего не нашли
        if not actions:
            seen_queries: set[str] = set()

            for m in _RE_RU_SEARCH.finditer(text):
                q = m.group(1).strip().rstrip('.,:;')[:200]
                if q and q not in seen_queries:
                    actions.append({'type': 'search', 'input': q, 'source': 'ru_plan'})
                    seen_queries.add(q)

            for m in _RE_RU_READ.finditer(text):
                p = m.group(1).strip().rstrip('.,:;')
                if p:
                    actions.append({'type': 'read', 'input': p, 'source': 'ru_plan'})

            for m in _RE_RU_BASH.finditer(text):
                cmd = m.group(1).strip()
                if cmd:
                    actions.append({'type': 'bash', 'input': cmd[:_MAX_CMD_LEN],
                                    'source': 'ru_plan'})

            for m in _RE_RU_PYTHON.finditer(text):
                code = m.group(1).strip()
                if code:
                    actions.append({'type': 'python', 'input': code[:_MAX_CODE_LEN],
                                    'source': 'ru_plan'})

            if actions:
                self._log(
                    f"[extract] Маркеров не найдено — распознал {len(actions)} действий "
                    f"из русскоязычного плана (резервный парсер)."
                )

        return actions

    # ── Исполнение одного действия ────────────────────────────────────────────

    def _execute_action(self, action: dict) -> dict:
        """Маршрутизирует одно действие к нужному инструменту."""
        t = action['type']

        # ── ContentSanitizer: AST/regex валидация ПЕРЕД исполнением ────────
        if t == 'python':
            ok, reason = _ContentSanitizer.validate_python(action['input'])
            if not ok:
                # SyntaxError — LLM сгенерировал невалидный Python.
                # Если есть LLM — просим исправить код (одна попытка).
                if 'SyntaxError' in reason and self._llm:
                    fixed_code = self._retry_fix_syntax(action['input'], reason)
                    if fixed_code:
                        ok2, reason2 = _ContentSanitizer.validate_python(fixed_code)
                        if ok2:
                            self._log('[python] SyntaxError автоисправлен LLM-retry')
                            action = {**action, 'input': fixed_code}
                        else:
                            self._log(f'[python] LLM-retry не помог: {reason2}')
                            return self._fail('python', action['input'][:200],
                                              f'ContentSanitizer: {reason}')
                    else:
                        return self._fail('python', action['input'][:200],
                                          f'ContentSanitizer: {reason}')
                else:
                    self._log(f'[python] BLOCKED by ContentSanitizer: {reason}')
                    return self._fail('python', action['input'][:200],
                                      f'ContentSanitizer: {reason}')
        elif t == 'bash':
            ok, reason = _ContentSanitizer.validate_bash(action['input'])
            if not ok:
                self._log(f'[bash] BLOCKED by ContentSanitizer: {reason}')
                return self._fail('bash', action['input'][:200],
                                  f'ContentSanitizer: {reason}')

        if t == 'build_module':
            return self._run_build_module(action['input'], action.get('description', ''))
        elif t == 'bash':
            return self._run_bash(action['input'])
        elif t == 'python':
            return self._run_python(action['input'])
        elif t == 'search':
            return self._run_search(action['input'])
        elif t == 'read':
            return self._run_read(action['input'])
        elif t == 'write':
            return self._run_write(action['input'], action.get('content', ''))
        else:
            return self._fail(t, action.get('input', ''), f"Неизвестный тип: {t}")

    # ── Авто-исправление синтаксических ошибок через LLM ─────────────────────

    def _retry_fix_syntax(self, broken_code: str, error_msg: str) -> str | None:
        """Просит LLM исправить SyntaxError в коде. Возвращает исправленный код или None."""
        if not self._llm:
            return None
        try:
            prompt = (
                "Следующий Python-код содержит синтаксическую ошибку:\n"
                f"Ошибка: {error_msg}\n\n"
                f"```python\n{broken_code[:3000]}\n```\n\n"
                "Исправь синтаксическую ошибку и верни ТОЛЬКО исправленный Python-код "
                "внутри ```python ... ```. Ничего не объясняй."
            )
            response = self._llm.infer(prompt)
            if not response:
                return None
            # Извлекаем код из ответа
            m = re.search(r'```python\n(.*?)```', str(response), re.DOTALL)
            if m:
                return m.group(1).strip()
            # Если нет блока — берём весь ответ как код (если похоже на Python)
            resp = str(response).strip()
            if resp.startswith(('import ', 'from ', '#', 'def ', 'class ')):
                return resp
            return None
        except Exception as e:
            self._log(f'[python] LLM retry failed: {e}')
            return None

    # ── Исполнители по типам ──────────────────────────────────────────────────

    def _run_bash(self, command: str) -> dict:
        """Запускает shell-команду через TerminalTool или ExecutionSystem."""
        command_to_run = command
        if self._is_windows():
            # Если WSL доступен и команда Linux-специфичная — запускаем через wsl
            if self._wsl_available and self._looks_unix_only(command):
                command_to_run = f'wsl {command}'
            else:
                adapted = self._adapt_bash_for_windows(command)
                mode = adapted.get('mode')

                if mode == 'python':
                    return self._run_python(adapted.get('code', ''))

                if mode == 'powershell':
                    return self._run_powershell(adapted.get('command', command))

                if mode == 'skip':
                    return {
                        'type':    'bash',
                        'input':   command,
                        'output':  '',
                        'success': True,
                        'error':   None,
                        'note':    adapted.get('note', 'Bash-команда пропущена на Windows'),
                    }

                command_to_run = adapted.get('command', command)

        if self.tool_layer:
            raw = self.tool_layer.use('terminal', command=command_to_run, timeout=30)
            if isinstance(raw, dict):
                output = raw.get('stdout', '') or raw.get('output', '')
                stderr = raw.get('stderr', '')
                ok     = raw.get('success', raw.get('returncode', 0) == 0)

                if not ok and self._is_windows() and self._looks_unix_only(command_to_run):
                    return {
                        'type':    'bash',
                        'input':   command,
                        'output':  output,
                        'stderr':  stderr,
                        'success': True,
                        'error':   None,
                        'note':    'Пропущена Linux-only bash-команда на Windows',
                    }

                # На Windows: если команда упала без сообщения — пробуем PowerShell
                if not ok and self._is_windows() and not stderr and not output:
                    ps_result = self._try_powershell_fallback(command)
                    if ps_result is not None:
                        return ps_result
                    note_msg = f'[BASH НЕ ВЫПОЛНЕНА: команда "{command[:60]}" не дала вывода на Windows. Используйте PYTHON или PowerShell-команду.]'
                    return {
                        'type':    'bash',
                        'input':   command,
                        'output':  note_msg,
                        'success': True,
                        'error':   None,
                        'note':    'Bash-команда завершилась без вывода на Windows (пропущена)',
                    }

                return {
                    'type':    'bash',
                    'input':   command_to_run,
                    'output':  output,
                    'stderr':  stderr,
                    'success': bool(ok),
                    'error':   stderr if not ok else None,
                }

        # Fallback: ExecutionSystem
        if self.execution_system:
            task = self.execution_system.submit(command_to_run, task_type='command',
                                                timeout=30)
            from execution.execution_system import TaskStatus
            ok = task.status == TaskStatus.SUCCESS

            if not ok and self._is_windows() and self._looks_unix_only(command_to_run):
                return {
                    'type':    'bash',
                    'input':   command,
                    'output':  task.stdout or '',
                    'stderr':  task.stderr or '',
                    'success': True,
                    'error':   None,
                    'note':    'Пропущена Linux-only bash-команда на Windows',
                }

            return {
                'type':    'bash',
                'input':   command_to_run,
                'output':  task.stdout or '',
                'stderr':  task.stderr or '',
                'success': ok,
                'error':   task.error if not ok else None,
            }

        return self._fail('bash', command, 'ни tool_layer, ни execution_system не подключены')

    def _try_powershell_fallback(self, command: str) -> dict | None:
        """Пробует выполнить PowerShell-эквивалент bash-команды. Возвращает dict или None."""
        adapted = self._powershell_event_log(command)
        if adapted is None:
            return None
        ps_cmd = adapted
        try:
            from execution.command_gateway import CommandGateway
            gw = CommandGateway.get_instance()
            r = gw.execute(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_cmd],
                timeout=20, caller='ActionDispatcher._try_powershell_fallback',
            )
            if not r.allowed:
                return None
            output = (r.stdout or '').strip()
            stderr = (r.stderr or '').strip()
            ok = r.returncode == 0
            return {
                'type':    'bash',
                'input':   command,
                'output':  output if ok else f'[PowerShell: {stderr[:200]}]',
                'success': ok,
                'error':   stderr if not ok else None,
                'note':    f'PowerShell fallback: {ps_cmd[:80]}',
            }
        except (OSError, Exception):
            return None

    @staticmethod
    def _powershell_event_log(command: str) -> str | None:
        """Возвращает PowerShell-эквивалент для event log команд, иначе None."""
        low = command.lower()
        # wevtutil / Get-EventLog / Get-WinEvent / eventlog
        if any(kw in low for kw in ('wevtutil', 'get-eventlog', 'get-winevent', 'eventlog')):
            return (
                r"Get-WinEvent -LogName Application -MaxEvents 20 -ErrorAction SilentlyContinue "
                r"| Where-Object {$_.LevelDisplayName -in 'Error','Critical'} "
                r"| Select-Object TimeCreated,LevelDisplayName,Message "
                r"| Format-List | Out-String -Width 200"
            )
        # journalctl на Windows
        if 'journalctl' in low:
            return (
                r"Get-WinEvent -LogName System -MaxEvents 20 -ErrorAction SilentlyContinue "
                r"| Select-Object TimeCreated,LevelDisplayName,Message "
                r"| Format-List | Out-String -Width 200"
            )
        return None

    @staticmethod
    def _is_windows() -> bool:
        return os.name == 'nt'

    @staticmethod
    def _check_wsl() -> bool:
        """Проверяет доступность WSL на Windows."""
        if os.name != 'nt':
            return False
        try:
            result = subprocess.run(
                ['wsl', '--status'],
                capture_output=True, timeout=5, creationflags=0x08000000,
                check=False,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            try:
                result = subprocess.run(
                    ['wsl', 'echo', 'ok'],
                    capture_output=True, timeout=5, creationflags=0x08000000,
                    check=False,
                )
                return b'ok' in result.stdout
            except (OSError, subprocess.SubprocessError):
                return False

    @staticmethod
    def _looks_unix_only(command: str) -> bool:
        cmd = f" {(command or '').lower()} "
        unix_markers = (
            ' df ', ' free ', ' ps aux', ' grep ', ' awk ', ' sed ',
            ' bash ', ' sh ', ' /proc/', ' /sys/',
        )
        return any(marker in cmd for marker in unix_markers)

    @staticmethod
    def _adapt_bash_for_windows(command: str) -> dict:
        """Пытается адаптировать частые bash-команды для Windows.
        Возвращает dict с mode: command|python|skip.
        """
        cmd = (command or '').strip()
        low = re.sub(r'\s+', ' ', cmd.lower())

        if 'df -h' in low or low == 'df':
            return {
                'mode': 'python',
                'code': (
                    "import psutil\n"
                    "du = psutil.disk_usage('C:\\\\')\n"
                    "print(f'Disk C:: total={du.total}, used={du.used}, free={du.free}, percent={du.percent}')\n"
                ),
            }

        if 'free -h' in low or low == 'free':
            return {
                'mode': 'python',
                'code': (
                    "import psutil\n"
                    "vm = psutil.virtual_memory()\n"
                    "print(f'RAM: total={vm.total}, used={vm.used}, available={vm.available}, percent={vm.percent}')\n"
                ),
            }

        if low.startswith('ps aux') or low == 'ps':
            return {
                'mode': 'python',
                'code': (
                    "import psutil\n"
                    "rows = []\n"
                    "for p in psutil.process_iter(['pid', 'name', 'cpu_percent']):\n"
                    "    try:\n"
                    "        info = p.info\n"
                    "        rows.append((info.get('cpu_percent') or 0.0, info.get('pid'), info.get('name')))\n"
                    "    except (psutil.NoSuchProcess, psutil.AccessDenied):\n"
                    "        pass\n"
                    "rows.sort(reverse=True)\n"
                    "for cpu, pid, name in rows[:10]:\n"
                    "    print(f'{pid}\t{name}\tCPU={cpu}')\n"
                ),
            }

        if low == 'pwd':
            return {'mode': 'command', 'command': 'cd'}

        if low == 'ls':
            return {'mode': 'command', 'command': 'dir'}

        if low.startswith('ls '):
            return {'mode': 'command', 'command': f"dir {cmd[3:].strip()}"}

        # PowerShell-командлеты в bash-блоке — запускаем через powershell.exe
        _ps_cmdlets = (
            'get-process', 'get-service', 'get-childitem', 'get-item',
            'select-object', 'where-object', 'format-list', 'format-table',
            'get-content', 'set-content', 'out-file', 'get-date',
            'get-host', 'get-computerinfo', 'get-volume', 'get-disk',
            'get-netadapter', 'get-wmiobject', 'invoke-expression',
            'start-process', 'stop-process',
        )
        if any(kw in low for kw in _ps_cmdlets):
            return {'mode': 'powershell', 'command': cmd}

        # Write-Output / Write-Host — PowerShell-команды вывода текста: конвертируем в print()
        if low.startswith('write-output ') or low.startswith('write-host '):
            m = re.match(r'^write-(?:output|host)\s+["\']?(.*?)["\']?\s*$', cmd, re.IGNORECASE | re.DOTALL)
            text = m.group(1) if m else cmd
            text_escaped = text.replace('\\', '\\\\').replace('"', '\\"')
            return {
                'mode': 'python',
                'code': f'print("{text_escaped}")\n',
            }

        # Event log команды -> делегируем в PowerShell через _powershell_event_log
        if any(kw in low for kw in ('get-eventlog', 'get-winevent', 'wevtutil', 'journalctl', 'eventlog')):
            return {'mode': 'powershell', 'command': (
                r"Get-WinEvent -LogName Application -MaxEvents 20 -ErrorAction SilentlyContinue "
                r"| Where-Object {$_.LevelDisplayName -in 'Error','Critical'} "
                r"| Select-Object TimeCreated,LevelDisplayName,Message "
                r"| Format-List | Out-String -Width 200"
            )}

        # Цепочки: cd <dir> && python <script>.py → конвертируем в прямой вызов
        _cd_and_py = re.match(
            r'^cd\s+([\w\-./\\]+)\s*&&\s*python(?:3)?\s+([\w\-./\\]+\.py)(.*)',
            cmd, re.IGNORECASE
        )
        if _cd_and_py:
            subdir  = _cd_and_py.group(1).strip()
            script  = _cd_and_py.group(2).strip()
            rest    = _cd_and_py.group(3)
            # Собираем полный путь к скрипту
            full_script = os.path.join(subdir, script) if not os.path.isabs(script) else script
            full_script = full_script.replace('/', os.sep)
            if not os.path.exists(full_script):
                alt = os.path.join(_DEFAULT_OUTPUT_DIR, os.path.basename(script))
                if os.path.exists(alt):
                    full_script = alt
            return {'mode': 'command', 'command': f'python "{full_script}"{rest}'}

        # Общие && цепочки → конвертируем в PowerShell через ; разделитель
        if '&&' in cmd:
            ps_cmd = cmd.replace('&&', ';')
            return {'mode': 'powershell', 'command': ps_cmd}

        # python <script>.py — если файл не в корне, ищем в outputs/
        _py_run = re.match(r'^python(?:3)?\s+([\w\-/\\\.]+\.py)(.*)', cmd, re.IGNORECASE)
        if _py_run:
            script = _py_run.group(1).strip().replace('/', os.sep).replace('\\', os.sep)
            rest   = _py_run.group(2)  # аргументы после имени файла
            if not os.path.isabs(script) and not os.path.exists(script):
                fallback = os.path.join(_DEFAULT_OUTPUT_DIR, os.path.basename(script))
                if os.path.exists(fallback):
                    script = fallback
            return {'mode': 'command', 'command': f'python "{script}"{rest}'}

        if ActionDispatcher._looks_unix_only(cmd):
            return {
                'mode': 'skip',
                'note': 'Несовместимая Linux-only bash-команда пропущена на Windows',
            }

        return {'mode': 'command', 'command': cmd}

    def _run_powershell(self, command: str) -> dict:
        """Запускает PowerShell-команду напрямую через powershell.exe."""
        cmd = (command or '').strip()
        low = cmd.lower()
        if not cmd:
            return self._fail('bash', command, 'Пустая PowerShell-команда')
        # Разрешаем только read-only подмножество командлетов.
        if any(tok in low for tok in _PS_RISKY_TOKENS):
            return self._fail('bash', command, 'PowerShell-команда отклонена политикой безопасности')
        # SECURITY: strict get- prefix check (no 'get-winevent' special case)
        if not low.startswith('get-'):
            return self._fail('bash', command, 'Разрешены только read-only PowerShell-команды (get-*)')

        # SECURITY: дополнительно блокируем цепочки/пайпы в read-only команде тоже
        # (уже проверено через _PS_RISKY_TOKENS выше, но двойная проверка)
        try:
            from execution.command_gateway import CommandGateway
            gw = CommandGateway.get_instance()
            r = gw.execute(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
                timeout=30, caller='ActionDispatcher._run_powershell',
            )
            output = (r.stdout or '').strip()
            stderr = (r.stderr or '').strip()
            if not r.allowed:
                return self._fail('bash', command, r.reject_reason)
            ok = r.returncode == 0
            return {
                'type':    'bash',
                'input':   cmd,
                'output':  output if ok else f'[PowerShell error: {stderr[:200]}]',
                'success': ok,
                'error':   stderr if not ok else None,
            }
        except (OSError, subprocess.SubprocessError) as e:
            return self._fail('bash', command, f'PowerShell недоступен: {e}')

    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def _fix_triple_quotes(code: str) -> str | None:
        # Пытается закрыть незакрытую тройную кавычку в конце кода.
        # Использует ast.parse для верификации вместо подсчёта вхождений
        # (подсчёт ненадёжен: тройная кавычка одного вида может быть внутри другой).
        # Возвращает исправленный код или None, если исправление невозможно.
        for q in ('"""', "'''"):
            candidate = code.rstrip() + '\n' + q
            try:
                ast.parse(candidate)
                return candidate
            except SyntaxError:
                pass
        return None

    @staticmethod
    def _fix_truncated_string(code: str) -> str | None:
        """Пытается спасти код, обрезанный по лимиту токенов.

        Стратегия: удаляем последнюю незавершённую строку и закрываем
        все незакрытые одиночные/двойные кавычки + восстанавливаем
        незакрытые скобки/фигурные скобки, чтобы получить валидный AST.
        """
        lines = code.splitlines()
        # Пробуем удалять строки снизу пока не получим валидный AST (макс 10 строк)
        for drop in range(1, min(11, len(lines))):
            candidate = '\n'.join(lines[:-drop])
            if not candidate.strip():
                break
            # Пробуем закрыть незакрытые кавычки/скобки
            for suffix in ('', '"', "'", ')', ']', '}', '")', "')"):
                try:
                    ast.parse(candidate + suffix)
                    return candidate + suffix if suffix else candidate
                except SyntaxError:
                    pass
        return None

    def _run_build_module(self, name: str, description: str) -> dict:
        """Создаёт новый Python-модуль через ModuleBuilder."""
        if not self.module_builder:
            return {
                'type': 'build_module', 'input': name,
                'output': '', 'success': False,
                'error': 'module_builder не подключён к ActionDispatcher',
            }
        try:
            result = self.module_builder.build_module(name=name, description=description)
            return {
                'type': 'build_module',
                'input': name,
                'output': result.file_path or '',
                'success': result.ok,
                'error': result.error if not result.ok else None,
            }
        except (AttributeError, TypeError, RuntimeError, ValueError, OSError) as e:
            return {
                'type': 'build_module', 'input': name,
                'output': '', 'success': False, 'error': str(e),
            }

    def _run_python(self, code: str) -> dict:
        """Запускает Python-код через PythonRuntimeTool."""
        if self.tool_layer:
            raw = self.tool_layer.use('python_runtime', code=code)
            if isinstance(raw, dict):
                output = raw.get('output', '') or str(raw.get('result', ''))
                ok     = raw.get('success', True)
                err    = raw.get('error', '') or ''

                # ── Авто-исправление 1: незакрытая тройная кавычка ──────────
                if not ok and 'unterminated triple-quoted string' in err:
                    fixed = self._fix_triple_quotes(code)
                    if fixed:
                        raw2 = self.tool_layer.use('python_runtime', code=fixed)
                        if isinstance(raw2, dict) and raw2.get('success'):
                            output = raw2.get('output', '') or str(raw2.get('result', ''))
                            return {
                                'type':    'python',
                                'input':   fixed,
                                'output':  output,
                                'success': True,
                                'error':   None,
                            }

                # ── Авто-исправление 1b: обычная незакрытая строка (обрезка по токенам) ──
                if not ok and 'unterminated string literal' in err:
                    fixed = self._fix_truncated_string(code)
                    if fixed:
                        raw2 = self.tool_layer.use('python_runtime', code=fixed)
                        if isinstance(raw2, dict) and raw2.get('success'):
                            output = raw2.get('output', '') or str(raw2.get('result', ''))
                            return {
                                'type':    'python',
                                'input':   fixed,
                                'output':  output,
                                'success': True,
                                'error':   None,
                            }

                # ── Авто-исправление 2: FileNotFoundError — создаём недостающий файл ──
                if not ok and 'No such file or directory' in err:
                    _fn = re.search(r"No such file or directory: '([^']+)'", err)
                    if _fn:
                        _missing = _fn.group(1).replace('\\\\', '\\')
                        try:
                            _dir = os.path.dirname(_missing)
                            if _dir:
                                os.makedirs(_dir, exist_ok=True)
                            if not os.path.exists(_missing):
                                # JSON-файлы инициализируем пустым массивом
                                _init = '[]' if _missing.endswith('.json') else ''
                                with open(_missing, 'w', encoding='utf-8') as _f:
                                    _f.write(_init)
                            raw2 = self.tool_layer.use('python_runtime', code=code)
                            if isinstance(raw2, dict) and raw2.get('success'):
                                output2 = raw2.get('output', '') or str(raw2.get('result', ''))
                                return {
                                    'type':    'python',
                                    'input':   code,
                                    'output':  output2,
                                    'success': True,
                                    'error':   None,
                                }
                        except OSError:
                            pass

                return {
                    'type':    'python',
                    'input':   code,
                    'output':  output,
                    'success': bool(ok),
                    'error':   err if not ok else None,
                }

        # FAIL-CLOSED: запрещаем локальный exec без песочницы.
        return self._fail(
            'python',
            code,
            'PythonRuntimeTool недоступен: исполнение кода без песочницы запрещено политикой безопасности',
        )

    # ── Relevance gate constants ───────────────────────────────────────────────
    # Спамовые домены, которые НЕ являются ответом на конкретный запрос о данных
    _SPAM_DOMAINS = (
        'youtube.com', 'vk.com', 'ok.ru', 'rutube.ru',
        'pinterest.', 'instagram.', 'tiktok.', 'facebook.com',
        'reddit.com',   # ok если нужно, но плох как источник фактов
    )
    # Паттерны в title/snippet — признак нерелевантного HowTo вместо данных
    _HOWTO_MARKERS = (
        'как искать', 'как найти', 'how to find', 'how to search',
        'what is', 'что такое', 'руководство', 'tutorial',
        'guide to', 'tips for', 'best way to', 'топ-10', 'топ 10',
        'best practices',
    )

    def _run_search(self, query: str) -> dict:
        """Выполняет поиск через SearchTool. Fallback: DuckDuckGo HTML.

        Включает relevance gate: если все результаты нерелевантны цели — помечает
        success=False с reason='IRRELEVANT_RESULTS'.
        """
        # ── Guard: не ищем в интернете очевидные локальные имена файлов ──────
        _q = query.strip().strip('"\'')
        if re.match(r'^[\w\-\.]+\.(txt|log|json|csv|py|md|yaml|yml|xml|ini|cfg|toml)$', _q, re.I):
            self._log(f'[search] SKIP: "{_q}" — похоже на локальный файл, веб-поиск пропущен.')
            return {
                'type': 'search', 'input': query, 'output': '',
                'success': False, 'error': f'LOCAL_FILENAME: "{_q}" — не ищем в интернете.',
            }
        if self.tool_layer:
            raw = self.tool_layer.use('search', query=query, num_results=5)
            if isinstance(raw, dict):
                results = raw.get('results', [])
                ok      = raw.get('success', bool(results))
                # Retry с упрощённым запросом если нет результатов
                if not results and not ok:
                    short_query = ' '.join(query.split()[:4])
                    if short_query and short_query != query:
                        raw2 = self.tool_layer.use('search', query=short_query, num_results=5)
                        if isinstance(raw2, dict) and raw2.get('results'):
                            raw, results = raw2, raw2.get('results', [])
                            ok = True
                if results:
                    # Relevance gate: отфильтровываем нерелевантные результаты
                    goal = self._current_goal
                    relevant_results = self._filter_relevant_results(results, query, goal)
                    if not relevant_results:
                        # Все результаты нерелевантны — честная ошибка
                        self._log(
                            f'[search] IRRELEVANT: запрос="{query[:60]}", '
                            f'цель="{goal[:60]}" — 0/{len(results)} релевантных.'
                        )
                        return {
                            'type':    'search',
                            'input':   query,
                            'output':  '',
                            'success': False,
                            'error':   'IRRELEVANT_RESULTS: результаты поиска не соответствуют задаче.',
                            'relevant': False,
                        }
                    output = '\n'.join(
                        f"- {r.get('title', '')}: {r.get('url', r.get('snippet', ''))}"
                        for r in relevant_results[:5]
                    )
                    # Сохраняем найденные URL в knowledge через acquisition_pipeline
                    self._queue_search_urls(relevant_results, query)
                    return {
                        'type':    'search',
                        'input':   query,
                        'output':  output,
                        'success': bool(ok),
                        'relevant': True,
                        'error':   None if ok else raw.get('error', 'No results found'),
                    }
                # tool_layer вернул пустой результат — пробуем HTTP fallback
        return self._search_duckduckgo_fallback(query)

    def _filter_relevant_results(self, results: list, query: str, goal: str) -> list:
        """Отфильтровывает нерелевантные search-результаты.

        Проверяет:
          1. Домен не в spam-list
          2. Title/snippet не является HowTo-статьёй вместо данных
          3. Хотя бы одно keyword из запроса/цели есть в title+snippet
        """
        if not results:
            return []

        # Ключевые слова из запроса и цели
        combined = f"{query} {goal}".lower()
        kw_pattern = re.findall(r'[a-zа-яё]{4,}', combined)
        # Убираем стоп-слова
        stop = {'найди', 'найти', 'поиск', 'поищи', 'search', 'find', 'about',
                'такое', 'that', 'this', 'with', 'from', 'what'}
        keywords = {w for w in kw_pattern if w not in stop}

        relevant = []
        for r in results:
            title   = str(r.get('title',   '') or '').lower()
            url     = str(r.get('url',     '') or '').lower()
            snippet = str(r.get('snippet', '') or '').lower()
            hay = f"{title} {snippet}"

            # 1. Спамовые домены
            if any(spam in url for spam in self._SPAM_DOMAINS):
                continue

            # 2. HowTo-маркеры в title: признак инструктажа вместо данных
            # НО только если в query не спрашивалось "как" (tutorial intent)
            query_is_howto = any(kw in query.lower() for kw in
                                 ('как ', 'how to', 'tutorial', 'guide', 'tips'))
            if not query_is_howto and any(m in hay for m in self._HOWTO_MARKERS):
                continue

            # 3. Keyword overlap — хотя бы 1 ключевое слово в результате
            if keywords and not any(kw in hay for kw in keywords):
                continue

            relevant.append(r)

        # Если ни один не прошёл — возвращаем исходные (не хотим зря обнулять)
        # только если фильтрация была слишком агрессивна (< 2 kw)
        if not relevant and len(keywords) < 2:
            return results
        return relevant

    def _queue_search_urls(self, results: list, query: str) -> None:
        """Добавляет URL из результатов поиска в acquisition_pipeline для сохранения в knowledge."""
        if not self.acquisition_pipeline:
            return
        query_tags = [w.lower() for w in query.split() if len(w) > 3][:3]
        for r in results[:3]:  # топ-3 результата
            url = str(r.get('url', '') or '').strip()
            if not url or not url.startswith('http'):
                continue
            # Пропускаем бинарные файлы
            if re.search(r'\.(pdf|jpg|jpeg|png|gif|zip|exe|mp4|mp3)($|\?)', url, re.IGNORECASE):
                continue
            try:
                tags = ['search', 'auto_acquired'] + query_tags
                self.acquisition_pipeline.add_source(url, source_type='web', tags=tags)
            except (AttributeError, TypeError, OSError, RuntimeError):
                pass

    def _search_duckduckgo_fallback(self, query: str) -> dict:
        """HTTP-fallback поиска через DuckDuckGo HTML когда SearchTool недоступен."""
        try:
            import urllib.request
            import urllib.parse
            import html as _html
            safe_q = urllib.parse.quote_plus((query or '')[:200])
            url = f"https://html.duckduckgo.com/html/?q={safe_q}"
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                body = resp.read().decode('utf-8', errors='replace')
            # Извлекаем заголовки и сниппеты
            # DDG: class="result__a" может быть не первым атрибутом → ищем по class=
            titles   = re.findall(r'class="result__a"[^>]*>(.*?)</a>', body, re.DOTALL)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>', body, re.DOTALL)
            if not titles:
                return self._fail('search', query, 'DDG fallback: нет результатов')
            pairs = list(zip(
                [_html.unescape(re.sub('<[^>]+>', '', t)).strip() for t in titles[:5]],
                [_html.unescape(re.sub('<[^>]+>', '', s)).strip() for s in (snippets[:5] + [''] * 5)],
            ))
            output = '\n'.join(
                f"- {title}: {snippet}" if snippet else f"- {title}"
                for title, snippet in pairs
                if title
            )
            if not output:
                return self._fail('search', query, 'DDG fallback: пустые результаты')
            return {
                'type':    'search',
                'input':   query,
                'output':  output,
                'success': True,
                'error':   None,
                'note':    'DuckDuckGo HTML fallback',
            }
        except (OSError, ValueError, AttributeError) as exc:
            return self._fail('search', query, f'SearchTool и DDG fallback недоступны: {exc}')

    # Нарезка по запросу (диапазон path:start-end) — авто-лимит отключён
    _READ_CHUNK_LINES     = 500   # шаг навигации при ручном запросе диапазона

    def _run_read(self, path: str) -> dict:
        """Читает файл через FileSystemTool. Поддерживает нарезку: path:start-end."""
        if not self.tool_layer:
            return self._fail('read', path, 'FileSystemTool не подключён')

        # Парсим диапазон строк из пути: "file.py:201-400"
        range_start: int | None = None
        range_end:   int | None = None
        raw_path = (path or '').strip()
        _range_m = re.search(r':(\d+)-(\d+)$', raw_path)
        if _range_m:
            range_start = int(_range_m.group(1))
            range_end   = int(_range_m.group(2))
            raw_path    = raw_path[:_range_m.start()]

        read_path = self._normalize_read_path(raw_path)

        # Проверяем существование файла
        exists_raw = self.tool_layer.use('filesystem', action='exists', path=read_path)
        if isinstance(exists_raw, dict) and not exists_raw.get('exists', True):
            msg = f'[ФАЙЛ НЕ НАЙДЕН: {read_path}. Сначала создайте его через WRITE, либо используйте другой путь.]'
            return {
                'type':    'read',
                'input':   read_path,
                'output':  msg,
                'success': True,
                'error':   None,
                'note':    f'Файл не существует: {read_path}',
            }

        raw = self.tool_layer.use('filesystem', action='read', path=read_path)
        if not isinstance(raw, dict):
            return self._fail('read', path, 'FileSystemTool не подключён')

        content = raw.get('content', '')
        ok      = raw.get('success', bool(content))

        if not ok:
            return {
                'type':    'read',
                'input':   read_path,
                'output':  content,
                'success': False,
                'error':   raw.get('error'),
            }

        lines = content.splitlines()
        total = len(lines)

        if range_start is not None and range_end is not None:
            # Запрошен конкретный диапазон строк
            s           = max(0, range_start - 1)
            e           = min(total, range_end)
            chunk_text  = '\n'.join(lines[s:e])
            if e < total:
                ns   = e + 1
                ne   = min(total, e + self._READ_CHUNK_LINES)
                hint = (f'\n\n[Показаны строки {range_start}–{e} из {total}. '
                        f'Следующий блок: READ: {read_path}:{ns}-{ne}]')
            else:
                hint = f'\n\n[Конец файла. Строки {range_start}–{e} из {total}.]'
            output = chunk_text + hint

        else:
            # Файл возвращается целиком без ограничений
            output = content

        return {
            'type':    'read',
            'input':   read_path,
            'output':  output,
            'success': True,
            'error':   None,
        }

    def _run_write(self, path: str, content: str) -> dict:
        """Записывает файл через FileSystemTool."""
        write_path = self._normalize_write_path(path)

        # ── Пустой контент для кодовых файлов отклоняется сразу ──────────
        if not (content or '').strip():
            ext = os.path.splitext(write_path)[1].lower()
            if ext in _CODE_EXTENSIONS:
                self._log(f"[write] EMPTY_REJECTED '{write_path}': пустой контент для кодового файла")
                return self._fail('write', write_path,
                                  "EMPTY_REJECTED: пустой контент для кодового файла")

        repeat_reject = self._find_recent_reject(write_path, content)
        if repeat_reject is not None:
            reason = str(repeat_reject.get('reason', 'previous rejection'))
            self._log(f"[write] MEMORY_REJECT '{write_path}': повтор ранее отклонённого файла ({reason})")
            return {
                'type':    'write',
                'input':   write_path,
                'output':  '',
                'success': False,
                'error':   f"MEMORY_REJECTED_REPEAT: ранее отклонено ({reason})",
            }

        # ── Dedup: если файл уже существует с тем же содержимым — пропускаем ──
        dup_result = self._check_content_duplicate(write_path, content)
        if dup_result is not None:
            return dup_result

        # Проверяем на заглушки для кодовых файлов
        stub_reason = self._detect_stub(write_path, content)
        if stub_reason:
            self._log(f"[write] STUB_REJECTED '{write_path}': {stub_reason} — пробую regenerate")
            # Retry: просим LLM написать настоящий код
            regenerated = self._regenerate_with_llm(write_path, stub_reason)
            if regenerated and not self._detect_stub(write_path, regenerated):
                self._log(f"[write] regenerate OK для '{write_path}' ({len(regenerated)} символов)")
                content = regenerated
            else:
                self._remember_reject(write_path, content, f"STUB_REJECTED: {stub_reason}")
                self._log(f"[write] regenerate не помог — STUB_REJECTED '{write_path}'")
                return {
                    'type':    'write',
                    'input':   write_path,
                    'output':  '',
                    'success': False,
                    'error':   (
                        f"STUB_REJECTED: {stub_reason}. "
                        f"Для файла '{write_path}' нужен реальный код в CONTENT, "
                        f"не описание и не заглушка."
                    ),
                }

        # Quality Gate: отсеиваем плохие/ломающие архитектуру файлы
        quality = self._assess_file_quality(write_path, content)
        if quality['score'] < 85:
            issues = '; '.join(quality['issues'][:5]) if quality['issues'] else 'нет деталей'
            self._log(
                f"[write] QUALITY_REJECTED '{write_path}': {quality['score']}% < 85% | {issues}"
            )
            self._remember_reject(
                write_path,
                content,
                f"QUALITY_REJECTED: score={quality['score']}% (<85%)",
                score=quality['score'],
                issues=quality['issues'],
            )
            return {
                'type':    'write',
                'input':   write_path,
                'output':  '',
                'success': False,
                'error': (
                    f"QUALITY_REJECTED: score={quality['score']}% (<85%). "
                    f"Проблемы: {issues}"
                ),
            }

        if self.tool_layer:
            if self._is_invalid_upwork_jobs_write(write_path, content):
                reason = (
                    "WRITE отклонён для outputs/upwork_jobs.txt: контент не похож на реальный список вакансий. "
                    "Нужен структурированный формат с полями: Title, Budget, Link, Posted, Summary "
                    "(минимум Link или Budget), без DSL-команд READ/WRITE/SEARCH/CONTENT."
                )
                self._remember_reject(write_path, content, reason)
                self._log(f"[write/upwork_jobs] {reason}")
                return self._fail(
                    'write',
                    write_path,
                    reason,
                )
            # Автоматически создаём родительские директории
            parent = os.path.dirname(write_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            raw = self.tool_layer.use('filesystem', action='write',
                                      path=write_path, content=content)
            if isinstance(raw, dict):
                ok = raw.get('success', 'written' in raw)
                return {
                    'type':    'write',
                    'input':   write_path,
                    'output':  raw.get('written', write_path),
                    'success': bool(ok),
                    'error':   raw.get('error') if not ok else None,
                }

        return self._fail('write', path, 'FileSystemTool не подключён')

    def _check_content_duplicate(self, write_path: str, content: str) -> dict | None:
        """Проверяет, существует ли файл с таким же или очень похожим содержимым.

        Возвращает dict-результат (skip) если дубликат найден, иначе None.
        Дополнительно проверяет соседние файлы в outputs/ на схожий контент
        (предотвращает cycle_report.md, report_cycle.md, cycle_1_report.md и т.д.)
        """
        # 1. Точный дубликат — тот же файл с тем же содержимым
        if os.path.isfile(write_path):
            try:
                existing = open(write_path, 'r', encoding='utf-8', errors='ignore').read()
                new_stripped = content.strip()
                old_stripped = existing.strip()
                if new_stripped == old_stripped:
                    self._log(f"[write] DEDUP_SKIP '{write_path}': файл уже существует с тем же содержимым")
                    return {
                        'type': 'write', 'input': write_path,
                        'output': write_path, 'success': True, 'error': None,
                        'note': 'DEDUP: файл уже существует с идентичным содержимым',
                    }
                # Jaccard-подобный n-gram dedup: если >85% совпадение — тоже пропускаем
                if len(new_stripped) > 50 and len(old_stripped) > 50:
                    new_set = set(new_stripped[i:i+4] for i in range(len(new_stripped) - 3))
                    old_set = set(old_stripped[i:i+4] for i in range(len(old_stripped) - 3))
                    intersection = len(new_set & old_set)
                    union = len(new_set | old_set)
                    if union and intersection / union > 0.85:
                        self._log(f"[write] DEDUP_SKIP '{write_path}': файл с >85% совпадением уже существует")
                        return {
                            'type': 'write', 'input': write_path,
                            'output': write_path, 'success': True, 'error': None,
                            'note': 'DEDUP: файл уже существует с >85% совпадением содержимого',
                        }
            except OSError:
                pass

        # 2. Дубликат по содержимому среди файлов в той же папке (outputs/ и т.д.)
        parent = os.path.dirname(write_path)
        if parent and os.path.isdir(parent):
            new_hash = hashlib.sha1(content.strip().encode('utf-8', errors='ignore')).hexdigest()
            try:
                for name in os.listdir(parent):
                    fp = os.path.join(parent, name)
                    if fp == write_path or not os.path.isfile(fp):
                        continue
                    try:
                        with open(fp, 'r', encoding='utf-8', errors='ignore') as fh:
                            existing_text = fh.read()
                        eh = hashlib.sha1(existing_text.strip().encode('utf-8', errors='ignore')).hexdigest()
                        if eh == new_hash:
                            self._log(
                                f"[write] DEDUP_SKIP '{write_path}': "
                                f"идентичный контент уже есть в '{name}'"
                            )
                            return {
                                'type': 'write', 'input': write_path,
                                'output': fp, 'success': True, 'error': None,
                                'note': f'DEDUP: идентичный контент уже в {name}',
                            }
                    except (OSError, UnicodeDecodeError):
                        continue
            except OSError:
                pass

        return None

    def _content_signature(self, path: str, content: str) -> str:
        base = f"{(path or '').strip().lower()}\n{(content or '').strip()}"
        return hashlib.sha1(base.encode('utf-8', errors='ignore')).hexdigest()

    def _find_recent_reject(self, path: str, content: str) -> dict | None:
        """Ищет недавний отказ по ПУТИ (для структурных ошибок типа upwork_jobs)
        или по точной СИГНАТУРЕ (для содержательных ошибок).
        """
        sig = self._content_signature(path, content)
        now = time.time()
        
        # Сначала ищем по точной сигнатуре (одинаковый контент)
        for item in reversed(self._reject_memory):
            if item.get('signature') != sig:
                continue
            ts = float(item.get('ts', 0.0))
            if now - ts <= self._reject_memory_window_sec:
                return item
            break
        
        # Если не нашли по сигнатуре, но файл был отклонён за FORMAT/STRUCTURE
        # (upwork, DSL-ошибки и т.д.), блокируем ВСЕ повторные попытки этого файла
        normalized_path = path.lower().replace('\\', '/')
        for item in reversed(self._reject_memory):
            item_path = (item.get('path') or '').lower().replace('\\', '/')
            if item_path != normalized_path:
                continue
            ts = float(item.get('ts', 0.0))
            if now - ts <= self._reject_memory_window_sec:
                # Блокируем если это была структурная ошибка (upwork, DSL)
                reason = str(item.get('reason', ''))
                if any(x in reason for x in ['upwork_jobs', 'структурированный', 'формат', 'DSL']):
                    return item  # Блокируем так же, как точное совпадение
            break
        
        return None

    def _remember_reject(
        self,
        path: str,
        content: str,
        reason: str,
        score: int | None = None,
        issues: list[str] | None = None,
    ) -> None:
        row = {
            'ts': time.time(),
            'path': path,
            'reason': reason,
            'signature': self._content_signature(path, content),
        }
        if score is not None:
            row['score'] = int(score)
        if issues:
            row['issues'] = [str(x)[:200] for x in issues[:8]]

        # SECURITY: вычищаем секреты из issues/reason перед персистингом
        if self._security and hasattr(self._security, 'scrub_text'):
            row['reason'] = self._security.scrub_text(row.get('reason', ''))
            if 'issues' in row:
                row['issues'] = [self._security.scrub_text(i) for i in row['issues']]

        self._reject_memory.append(row)
        if len(self._reject_memory) > 2000:
            self._reject_memory = self._reject_memory[-2000:]
        self._save_reject_memory()

    def _load_reject_memory(self) -> list[dict]:
        try:
            if not os.path.exists(self._reject_memory_path):
                return []
            with open(self._reject_memory_path, encoding='utf-8') as f:
                data = json.load(f)
            rows = data.get('rejects', []) if isinstance(data, dict) else []
            return rows if isinstance(rows, list) else []
        except (OSError, json.JSONDecodeError, ValueError):
            return []

    def _save_reject_memory(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._reject_memory_path), exist_ok=True)
            with open(self._reject_memory_path, 'w', encoding='utf-8') as f:
                json.dump({'rejects': self._reject_memory[-2000:]}, f, ensure_ascii=False)
        except (OSError, TypeError, ValueError):
            pass

    # ── Вспомогательные методы ────────────────────────────────────────────────

    def _regenerate_with_llm(self, path: str, stub_reason: str) -> str | None:
        """Просит LLM написать полный рабочий код для файла вместо заглушки."""
        if not self._llm:
            return None
        filename = os.path.basename(path)
        prompt = (
            f"Напиши полный рабочий Python-код для файла '{filename}'.\n"
            f"Причина запроса: предыдущая попытка была отклонена ({stub_reason}).\n\n"
            f"Требования:\n"
            f"- Только настоящий исполняемый Python-код, без заглушек и placeholder-ов\n"
            f"- Используй только стандартные библиотеки: os, pathlib, json, csv, "
            f"sqlite3, psutil, re, datetime, collections, itertools, math\n"
            f"- Код должен быть минимум 30 строк\n"
            f"- Верни ТОЛЬКО код в блоке ```python ... ``` без пояснений\n"
            f"- ОБЯЗАТЕЛЬНО: код должен синтаксически корректным Python, все скобки и кавычки закрыты"
        )
        try:
            # Всегда используем тяжёлый LLM для регенерации кода —
            # Qwen генерирует невалидный код и всегда проваливает quality gate
            raw = self._llm.infer(prompt, system='[HEAVY] код регенерация')
            if not raw:
                return None
            # Извлекаем код из ```python ... ```
            m = re.search(r'```python\s*\n([\s\S]+?)```', str(raw))
            if m:
                return m.group(1).strip()
            # Если нет маркеров — возвращаем весь ответ если он похож на код
            text = str(raw).strip()
            if len(text) > 30 and ('def ' in text or 'import ' in text):
                return text
        except (AttributeError, TypeError, RuntimeError, ValueError, OSError):
            pass
        return None

    @staticmethod
    def _fail(action_type: str, input_val: str, error: str) -> dict:
        return {
            'type':    action_type,
            'input':   str(input_val)[:200],
            'output':  '',
            'success': False,
            'error':   error,
        }

    @staticmethod
    def _is_invalid_upwork_jobs_write(path: str, content: str) -> bool:
        """Блокирует запись DSL/плейсхолдеров в outputs/upwork_jobs.txt."""
        normalized = (path or '').replace('\\', '/').lower()
        if not normalized.endswith('outputs/upwork_jobs.txt'):
            return False

        text = (content or '').strip()
        if not text:
            return True

        junk_markers = (
            '```',
            'READ:',
            'WRITE:',
            'SEARCH:',
            'CONTENT:',
            'PYTHON:',
            'BASH:',
            'New job listings with budget',
            'budget and links',
            'Название работы:\n',
        )
        if any(marker in text for marker in junk_markers):
            return True

        has_link = bool(re.search(r'https?://', text))
        has_budget = bool(re.search(r'\$\d|бюджет|budget|\d+\s*(usd|\$)', text, re.IGNORECASE))
        return not (has_link or has_budget)

    @staticmethod
    def _detect_stub(path: str, content: str) -> str | None:
        """
        Возвращает описание проблемы если content — заглушка.
        Универсальные фразы-заглушки проверяются для ВСЕХ типов файлов.
        Специфичные для кода проверки — только для кодовых файлов.
        """
        stripped_lc = (content or '').strip().lower()

        # Универсальная проверка (все типы файлов): явные placeholder-фразы от LLM
        for phrase in _UNIVERSAL_STUB_PHRASES:
            if phrase in stripped_lc:
                return f"содержит заглушку '{phrase}'"

        ext = os.path.splitext(path)[1].lower()
        if ext not in _CODE_EXTENSIONS:
            return None  # остальные проверки только для кодовых файлов

        stripped = (content or '').strip()

        # Пустой контент
        if not stripped:
            return "пустой контент"

        # Только открывающий маркер code block без тела
        if re.match(r'^```\w*\s*(?:```)?$', stripped):
            return "пустой code block"

        # Очень короткий контент для кода
        if len(stripped) < 30:
            return f"слишком короткий контент ({len(stripped)} символов)"

        # Только комментарии, без выполняемых операторов
        lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
        comment_markers = ('#', '//', '/*', '*', '"""', "'''")
        if lines and all(
            any(ln.startswith(m) for m in comment_markers) or ln in ('"""', "'''")
            for ln in lines
        ):
            return "только комментарии без реального кода"

        # Фразы-заглушки (только если контент небольшой, иначе текст легитимный)
        lower = stripped.lower()
        if len(stripped) < 300:
            for phrase in _STUB_PHRASES:
                if phrase in lower:
                    return f"обнаружена заглушка: '{phrase}'"

        return None

    def _assess_file_quality(self, path: str, content: str) -> dict:
        """Оценивает качество файла (0..100) и причины, почему файл плохой."""
        score = 100
        issues: list[str] = []

        ext = os.path.splitext(path)[1].lower()
        text = (content or '')
        stripped = text.strip()
        normalized_path = (path or '').replace('\\', '/').lower()

        # Общие признаки "хренового" файла
        if not stripped:
            score -= 80
            issues.append('пустой файл')

        placeholder_hits = [p for p in _STUB_PHRASES if p in stripped.lower()]
        if placeholder_hits:
            score -= min(35, 8 * len(placeholder_hits))
            issues.append(f"заглушки/placeholder: {', '.join(placeholder_hits[:3])}")

        todo_count = len(re.findall(r'\b(?:todo|fixme|stub|tbd)\b', stripped, re.IGNORECASE))
        if todo_count:
            score -= min(15, todo_count * 3)
            issues.append(f"TODO/FIXME/STUB: {todo_count}")

        if ext == '.py':
            # Синтаксис Python — базовый критерий годности
            try:
                ast.parse(text or '\n')
            except SyntaxError as e:
                score -= 70
                issues.append(f"синтаксическая ошибка Python: line {getattr(e, 'lineno', '?')}")

            line_count = len([ln for ln in text.splitlines() if ln.strip()])
            if line_count < 20:
                score -= 10
                issues.append(f"слишком мало содержательных строк: {line_count}")

            pass_lines = len(re.findall(r'^\s*pass\s*(#.*)?$', text, re.MULTILINE))
            if pass_lines >= 2:
                score -= min(20, pass_lines * 4)
                issues.append(f"много pass-заглушек: {pass_lines}")

            # Потенциально опасные конструкции (часто ломают архитектуру/безопасность)
            risky_patterns = [
                (r'\beval\s*\(', 20, 'используется eval()'),
                (r'\bexec\s*\(', 25, 'используется exec()'),
                (r'os\.system\s*\(', 20, 'используется os.system()'),
                (r'subprocess\.run\([^\)]*shell\s*=\s*True', 25, 'subprocess shell=True'),
                (r'pickle\.loads\s*\(', 12, 'небезопасный pickle.loads()'),
            ]
            low = text.lower()
            for pattern, penalty, msg in risky_patterns:
                if re.search(pattern, low, re.IGNORECASE | re.DOTALL):
                    score -= penalty
                    issues.append(msg)

            # Контекстные архитектурные проверки по директории
            if '/agents/' in normalized_path and not re.search(r'class\s+\w+Agent\b', text):
                score -= 15
                issues.append('для agents/ не найден класс *Agent')

            if '/tools/' in normalized_path and not re.search(r'class\s+\w+Tool\b', text):
                score -= 15
                issues.append('для tools/ не найден класс *Tool')

            if '/core/' in normalized_path and not re.search(r'\b(class|def)\b', text):
                score -= 12
                issues.append('для core/ нет class/def (похоже на мусорный файл)')

        score = max(0, min(100, score))
        return {'score': score, 'issues': issues}

    @staticmethod
    def _empty_result() -> dict:
        return {
            'actions_found':    0,
            'actions_executed': 0,
            'results':          [],
            'success':          True,
            'summary':          'Исполняемых блоков не найдено.',
        }

    @staticmethod
    def _make_summary(results: list) -> str:
        """Формирует краткую сводку для включения в контекст следующего цикла."""
        if not results:
            return 'Нет результатов.'
        lines = []
        for r in results:
            status = '✓' if r['success'] else '✗'
            output_preview = (r.get('output') or '')[:120].replace('\n', ' ')
            error_preview  = (r.get('error') or '')[:200]
            note_preview   = (r.get('note') or '')[:100]
            if r['success']:
                detail = output_preview or (f"[{note_preview}]" if note_preview else '')
            else:
                detail = f"Ошибка: {error_preview}"
            lines.append(f"{status} [{r['type']}] {detail}")
        return '\n'.join(lines)

    @staticmethod
    def _normalize_write_path(path: str) -> str:
        """Маршрутизирует относительные WRITE в папку outputs."""
        p = (path or '').strip().strip('"').strip("'")
        if not p:
            return f"{_DEFAULT_OUTPUT_DIR}/report_{int(time.time())}.txt"

        p = p.replace('\\', '/')
        p = p.replace('путь/к/файлу/', '').replace('path/to/file/', '')

        # Если путь без директории, пишем в outputs/...
        if not os.path.dirname(p):
            return f"{_DEFAULT_OUTPUT_DIR}/{os.path.basename(p)}"
        return p

    def _normalize_read_path(self, path: str) -> str:
        """Для READ пробует явный путь, затем fallback в outputs/."""
        p = (path or '').strip().strip('"').strip("'")
        if not p:
            return p
        p = p.replace('\\', '/')

        # Абсолютный путь с буквой диска (Windows) — например C:/application_log.txt
        # Если он не указывает внутрь рабочей директории, берём только basename → outputs/
        if re.match(r'^[A-Za-z]:/', p):
            wd = os.path.realpath(os.getcwd()).replace('\\', '/')
            if not p.startswith(wd):
                return f"{_DEFAULT_OUTPUT_DIR}/{os.path.basename(p)}"
            # Путь внутри рабочей директории — оставляем как есть
            return p

        # Путь без директории — ищем в outputs/
        if not os.path.dirname(p):
            if self.tool_layer is None:
                return f"{_DEFAULT_OUTPUT_DIR}/{p}"
            exists_raw = self.tool_layer.use('filesystem', action='exists', path=p)
            if isinstance(exists_raw, dict) and exists_raw.get('exists'):
                return p
            return f"{_DEFAULT_OUTPUT_DIR}/{p}"

        return p

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='action_dispatcher')
        else:
            print(f"[ActionDispatcher] {message}")
