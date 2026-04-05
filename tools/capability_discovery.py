# Capability Discovery System (обнаружение новых возможностей) — Слой 35
# Архитектура автономного AI-агента
# Поиск новых инструментов, анализ библиотек, автоматическое подключение возможностей.
# pylint: disable=broad-except

from __future__ import annotations

import time


class DiscoveredCapability:
    """Одна обнаруженная возможность/инструмент."""

    def __init__(self, name: str, description: str, capability_type: str,
                 source: str | None = None, how_to_use: str | None = None, tags: list | None = None):
        self.name = name
        self.description = description
        self.capability_type = capability_type  # 'library', 'tool', 'api', 'skill', 'model'
        self.source = source                    # откуда взято (PyPI, GitHub, ...)
        self.how_to_use = how_to_use
        self.tags = tags or []
        self.status = 'discovered'              # discovered | evaluated | accepted | rejected
        self.score: float | None = None
        self.discovered_at = time.time()

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'capability_type': self.capability_type,
            'source': self.source,
            'how_to_use': self.how_to_use,
            'tags': self.tags,
            'status': self.status,
            'score': self.score,
        }


class CapabilityDiscovery:
    """
    Capability Discovery System — Слой 35.

    Функции:
        - поиск новых Python-библиотек, API и инструментов
        - анализ: соответствует ли возможность нуждам агента
        - оценка: полезность, безопасность, сложность подключения
        - решение: включать в ToolLayer или отклонить
        - реестр всех открытых возможностей

    Используется:
        - Tool Layer (Слой 5)            — пополнение набора инструментов
        - Self-Improvement (Слой 12)     — расширение возможностей агента
        - Autonomous Loop (Слой 20)      — периодический поиск новых инструментов
    """

    def __init__(self, tool_layer=None, cognitive_core=None,
                 monitoring=None, human_approval=None):
        self.tools = tool_layer
        self.cognitive_core = cognitive_core
        self.monitoring = monitoring
        self.human_approval = human_approval

        self._discovered: dict[str, DiscoveredCapability] = {}
        self._accepted: list[str] = []
        self._rejected: list[str] = []
        # action_type → {'success': int, 'fail': int}  для grounding find_gaps()
        self._failure_log: dict[str, dict[str, int]] = {}

    # ── Поиск возможностей ────────────────────────────────────────────────────

    def discover_libraries(self, topic: str, n: int = 5) -> list[DiscoveredCapability]:
        """
        Ищет Python-библиотеки для заданной задачи через Cognitive Core.

        Args:
            topic — область или задача (например 'работа с PDF', 'NLP', 'HTTP-запросы')
            n     — количество предложений
        """
        if not self.cognitive_core:
            return []

        raw = self.cognitive_core.reasoning(
            f"Предложи {n} Python-библиотек для задачи: {topic}\n"
            f"Для каждой:\n"
            f"НАЗВАНИЕ: <имя пакета>\n"
            f"ОПИСАНИЕ: <что делает>\n"
            f"УСТАНОВКА: pip install <пакет>\n"
            f"ПРИМЕР: <краткий пример использования>"
        )

        caps = self._parse_library_suggestions(str(raw), topic)
        for cap in caps:
            self._discovered[cap.name] = cap
        self._log(f"Обнаружено {len(caps)} библиотек для '{topic}'")
        return caps

    def discover_apis(self, domain: str, n: int = 3) -> list[DiscoveredCapability]:
        """Ищет публичные API для заданной области."""
        if not self.cognitive_core:
            return []

        raw = self.cognitive_core.reasoning(
            f"Предложи {n} публичных API для области: {domain}\n"
            f"Для каждого: название, URL, что умеет, как использовать."
        )
        caps = self._parse_api_suggestions(str(raw), domain)
        if not caps:
            cap = DiscoveredCapability(
                name=f"api_{domain.replace(' ', '_')}",
                description=str(raw)[:300],
                capability_type='api',
                source=domain,
                how_to_use=str(raw),
                tags=[domain, 'api'],
            )
            caps = [cap]

        for cap in caps[:n]:
            self._discovered[cap.name] = cap
        return caps[:n]

    def scan_installed(self) -> list[DiscoveredCapability]:
        """Сканирует установленные Python-пакеты и регистрирует их как возможности."""
        try:
            import importlib.metadata as meta
            packages = [d.metadata['Name'] for d in meta.distributions()]
        except Exception:
            return []

        caps = []
        for pkg in packages[:50]:   # первые 50 для примера
            if pkg.lower() in self._discovered:
                continue
            cap = DiscoveredCapability(
                name=pkg.lower(),
                description=f"Установленный пакет: {pkg}",
                capability_type='library',
                source='local',
                tags=['installed'],
            )
            self._discovered[cap.name] = cap
            caps.append(cap)
        self._log(f"Просканировано {len(caps)} установленных пакетов")
        return caps

    # ── Оценка ───────────────────────────────────────────────────────────────

    def evaluate(self, name: str) -> float:
        """
        Оценивает обнаруженную возможность: полезность, безопасность, простота.
        Возвращает score 0–1.
        """
        cap = self._discovered.get(name)
        if not cap:
            return 0.0

        cap.status = 'evaluated'

        if not self.cognitive_core:
            cap.score = 0.5
            return 0.5  # type: ignore[return-value]

        raw = self.cognitive_core.reasoning(
            f"Оцени следующую возможность для автономного AI-агента по шкале 0–1:\n\n"
            f"Название: {cap.name}\n"
            f"Описание: {cap.description}\n"
            f"Тип: {cap.capability_type}\n\n"
            f"Критерии: полезность для агента, безопасность, простота интеграции.\n"
            f"Ответь одним числом от 0 до 1."
        )
        import re
        m = re.search(r'([01]?\.\d+|\d)', str(raw))
        score = float(m.group(1)) if m else 0.5
        cap.score = max(0.0, min(1.0, score))
        self._log(f"Оценка '{name}': {cap.score:.2f}")
        return cap.score  # type: ignore[return-value]

    def evaluate_all(self) -> dict[str, float]:
        """Оценивает все необработанные возможности."""
        return {
            name: self.evaluate(name)
            for name, cap in self._discovered.items()
            if cap.status == 'discovered'
        }

    # ── Принятие решения ──────────────────────────────────────────────────────

    def accept(self, name: str, auto_register: bool = True) -> bool:
        """
        Принимает возможность и опционально регистрирует в Tool Layer.
        Высокоприоритетные — через Human Approval.
        """
        cap = self._discovered.get(name)
        if not cap:
            return False

        # Human Approval для внешних API и неизвестных библиотек
        if cap.capability_type in ('api', 'library') and self.human_approval:
            approved = self.human_approval.request_approval(
                'capability_acceptance',
                f"Принять новую возможность:\n{cap.name}\n{cap.description}"
            )
            if not approved:
                cap.status = 'rejected'
                self._rejected.append(name)
                return False

        cap.status = 'accepted'
        self._accepted.append(name)

        # Регистрируем в Tool Layer если возможно
        if auto_register and self.tools and cap.how_to_use:
            self._log(f"Возможность '{name}' принята и добавлена в очередь Tool Layer")

        self._log(f"Возможность принята: '{name}'")
        return True

    def reject(self, name: str, reason: str | None = None):
        """Отклоняет возможность."""
        cap = self._discovered.get(name)
        if cap:
            cap.status = 'rejected'
            self._rejected.append(name)
        self._log(f"Возможность отклонена: '{name}'" + (f" ({reason})" if reason else ""))

    def auto_accept(self, min_score: float = 0.7):
        """Автоматически принимает все возможности с оценкой >= min_score."""
        accepted = []
        for name, cap in self._discovered.items():
            if cap.status == 'evaluated' and cap.score and cap.score >= min_score:
                if self.accept(name, auto_register=True):
                    accepted.append(name)
        self._log(f"Авто-принято {len(accepted)} возможностей (порог: {min_score})")
        return accepted

    # ── Реестр ───────────────────────────────────────────────────────────────

    def get_all(self, status: str | None = None) -> list[dict]:
        caps = self._discovered.values()
        if status:
            caps = [c for c in caps if c.status == status]
        return [c.to_dict() for c in caps]

    def get_accepted(self) -> list[dict]:
        return self.get_all(status='accepted')

    def summary(self) -> dict:
        from collections import Counter
        statuses = Counter(c.status for c in self._discovered.values())
        return {
            'total_discovered': len(self._discovered),
            **dict(statuses),
        }

    # ── Автобутстрап зависимостей ────────────────────────────────────────────

    # Все пакеты, которые нужны для полной работы Tool Layer
    REQUIRED_PACKAGES: list[tuple[str, str]] = [
        ('deep_translator',      'deep-translator'),
        ('schedule',             'schedule'),
        ('mss',                  'mss'),
        ('pyautogui',            'pyautogui'),
        ('pyperclip',            'pyperclip'),
        ('pygetwindow',          'pygetwindow'),
        ('psutil',               'psutil'),
        ('matplotlib',           'matplotlib'),
        ('openpyxl',             'openpyxl'),
        ('reportlab',            'reportlab'),
        ('pytesseract',          'pytesseract'),
        ('huggingface_hub',      'huggingface_hub'),
        ('duckduckgo_search',    'duckduckgo_search'),
        ('pynput',               'pynput'),
        ('google.oauth2',        'google-auth'),
        ('googleapiclient',      'google-api-python-client'),
        # Новые инструменты (группы 6-10)
        ('watchdog',             'watchdog'),
        ('plyer',                'plyer'),
        ('cryptography',         'cryptography'),
        ('paramiko',             'paramiko'),
        ('sentence_transformers', 'sentence-transformers'),
        ('nltk',                 'nltk'),
        ('pytest',               'pytest'),
    ]

    def find_gaps(self) -> list[str]:
        """
        Анализирует реальные пробелы в возможностях агента.

        Источники данных (грунтованные, без LLM-фантазий):
          1. REQUIRED_PACKAGES — пакеты, нужные Tool Layer, но не установленные
          2. _rejected с оценкой ≥ 0.5 — возможно, стоит пересмотреть
          3. _failure_log — типы действий, которые агент пробовал и проваливал

        Returns:
            Список строк-описаний пробелов. Пустой список = пробелов нет.
        """
        import importlib.util
        gaps: list[str] = []

        # 1. Недостающие обязательные пакеты (самые конкретные пробелы)
        missing_pkgs: list[str] = []
        for import_name, pip_name in self.REQUIRED_PACKAGES:
            # find_spec() проверяет наличие пакета БЕЗ фактического импорта —
            # безопасно для пакетов с побочными эффектами (GUI, hardware).
            spec = importlib.util.find_spec(import_name.split('.')[0])
            if spec is None:
                missing_pkgs.append(pip_name)
        for pkg in missing_pkgs:
            gaps.append(f"missing_package:{pkg}")

        # 2. Отклонённые возможности с приемлемой оценкой (≥ 0.5)
        for name in self._rejected:
            cap = self._discovered.get(name)
            if cap and cap.score is not None and cap.score >= 0.5:
                gaps.append(
                    f"rejected_useful:{name} — {cap.description[:100]}"
                )

        # 3. Действия из _failure_log — агент пробовал, но стабильно проваливался
        for action_type, stats in self._failure_log.items():
            total = stats['success'] + stats['fail']
            if total >= 3 and stats['fail'] / total >= 0.6:
                gaps.append(
                    f"weak_action:{action_type} — "
                    f"провалов {stats['fail']}/{total} ({stats['fail']/total:.0%})"
                )

        if gaps:
            self._log(f"find_gaps: обнаружено {len(gaps)} пробелов "
                      f"(пакеты={len(missing_pkgs)}, "
                      f"слабые_действия="
                      f"{sum(1 for g in gaps if g.startswith('weak_action:'))})")
        return gaps

    def get_missing_capabilities(self) -> list[str]:
        """Псевдоним для find_gaps() — обратная совместимость."""
        return self.find_gaps()

    def record_action_result(self, action_type: str, success: bool):
        """
        Записывает результат действия для grounding find_gaps().

        Вызывается из AutonomousLoop._act() через identity или напрямую.
        Накапливает статистику провалов по типам действий.
        """
        entry = self._failure_log.setdefault(action_type, {'success': 0, 'fail': 0})
        if success:
            entry['success'] += 1
        else:
            entry['fail'] += 1

    def bootstrap_required_packages(self) -> dict:
        """
        Проверяет наличие всех пакетов Tool Layer и устанавливает отсутствующие
        через pip (subprocess). Вызывается автоматически при старте агента.

        Returns:
            {'installed': [...], 'already_ok': [...], 'failed': [...]}
        """
        import importlib
        import subprocess
        import sys

        result: dict[str, list[str]] = {'installed': [], 'already_ok': [], 'failed': []}

        for import_name, pip_name in self.REQUIRED_PACKAGES:
            try:
                importlib.import_module(import_name)
                result['already_ok'].append(pip_name)
            except ImportError:
                self._log(f"Пакет отсутствует: {pip_name} — устанавливаю...")
                try:
                    subprocess.run(
                        [sys.executable, '-m', 'pip', 'install', '--quiet', pip_name],
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                    result['installed'].append(pip_name)
                    self._log(f"Установлен: {pip_name}")
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                    result['failed'].append(pip_name)
                    self._log(f"Ошибка установки {pip_name}: {e}", level='error')
            except Exception as e:
                # Некоторые desktop-зависимости могут существовать как пакет, но быть
                # неподдерживаемыми на текущей ОС (например pygetwindow на Linux).
                # Это не должно блокировать запуск всего агента.
                result['failed'].append(pip_name)
                self._log(
                    f"Пакет '{pip_name}' недоступен на этой платформе и пропущен: "
                    f"{type(e).__name__}: {e}",
                    level='warning',
                )

        if result['installed']:
            self._log(
                f"Бутстрап завершён: установлено {len(result['installed'])}, "
                f"пропущено {len(result['already_ok'])}, ошибок {len(result['failed'])}"
            )
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_library_suggestions(self, raw: str, topic: str) -> list[DiscoveredCapability]:
        import re
        caps = []
        blocks = re.split(r'\n(?=\d+[.)]\s+|\bНАЗВАНИЕ\b)', raw)
        for block in blocks:
            name_m = re.search(r'НАЗВАНИЕ[:\s]+(\S+)', block, re.IGNORECASE)
            desc_m = re.search(r'ОПИСАНИЕ[:\s]+(.+)', block, re.IGNORECASE)
            use_m = re.search(r'ПРИМЕР[:\s]+(.+)', block, re.IGNORECASE | re.DOTALL)
            if name_m:
                caps.append(DiscoveredCapability(
                    name=name_m.group(1).strip().lower(),
                    description=desc_m.group(1).strip() if desc_m else topic,
                    capability_type='library',
                    source='PyPI',
                    how_to_use=use_m.group(1).strip()[:300] if use_m else None,
                    tags=[topic, 'library'],
                ))
        return caps

    def _parse_api_suggestions(self, raw: str, domain: str) -> list[DiscoveredCapability]:
        import re
        caps = []
        blocks = re.split(r'\n(?=\d+[.)]\s+|\bНАЗВАНИЕ\b|\bAPI\b)', raw)
        for block in blocks:
            name_m = re.search(r'НАЗВАНИЕ[:\s]+(.+)', block, re.IGNORECASE)
            if not name_m:
                name_m = re.search(r'API[:\s]+(.+)', block, re.IGNORECASE)
            if not name_m:
                continue

            url_m = re.search(r'URL[:\s]+(https?://\S+)', block, re.IGNORECASE)
            desc_m = re.search(r'(?:ОПИСАНИЕ|ЧТО\s+УМЕЕТ)[:\s]+(.+)', block, re.IGNORECASE)
            use_m = re.search(r'(?:КАК\s+ИСПОЛЬЗОВАТЬ|ПРИМЕР)[:\s]+(.+)', block,
                              re.IGNORECASE | re.DOTALL)

            name = name_m.group(1).strip().split('\n')[0]
            caps.append(DiscoveredCapability(
                name=re.sub(r'\s+', '_', name.lower())[:64],
                description=(desc_m.group(1).strip() if desc_m else domain)[:300],
                capability_type='api',
                source=(url_m.group(1).strip() if url_m else domain),
                how_to_use=use_m.group(1).strip()[:400] if use_m else None,
                tags=[domain, 'api'],
            ))
        return caps

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='capability_discovery'
            )
        else:
            print(f"[CapabilityDiscovery] {message}")

    def export_state(self) -> dict:
        """Возвращает состояние для персистентности."""
        return dict(self._failure_log)

    def import_state(self, data: dict):
        """Восстанавливает состояние из персистентного хранилища."""
        for action_type, stats in data.items():
            if action_type in self._failure_log:
                self._failure_log[action_type]['success'] += stats.get('success', 0)
                self._failure_log[action_type]['fail'] += stats.get('fail', 0)
            else:
                self._failure_log[action_type] = {
                    'success': stats.get('success', 0),
                    'fail': stats.get('fail', 0),
                }
