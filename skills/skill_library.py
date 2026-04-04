# Skill / Capability Library (библиотека навыков) — Слой 29
# Архитектура автономного AI-агента
# Хранение готовых стратегий, повторное использование навыков,
# обучение новым навыкам, композиция навыков.


import json
import os
import pathlib
import time


class Skill:
    """Один навык — готовая стратегия или шаблон решения задачи."""

    def __init__(self, name: str, description: str, strategy: str,
                 tags: list | None = None, examples: list | None = None):
        self.name = name
        self.description = description
        self.strategy = strategy         # текстовое описание стратегии/шаблона
        self.tags = tags or []
        self.examples = examples or []   # примеры применения
        self.use_count = 0
        self.success_count = 0
        self.created_at = time.time()
        self.updated_at = time.time()

    @property
    def success_rate(self) -> float | None:
        if self.use_count == 0:
            return None
        return round(self.success_count / self.use_count, 2)

    def record_use(self, success: bool):
        self.use_count += 1
        if success:
            self.success_count += 1

    def to_dict(self):
        return {
            'name': self.name,
            'description': self.description,
            'strategy': self.strategy,
            'tags': self.tags,
            'examples': self.examples,
            'use_count': self.use_count,
            'success_rate': self.success_rate,
            'created_at': self.created_at,
        }


class SkillLibrary:
    """
    Skill / Capability Library — Слой 29.

    Функции:
        - хранение готовых стратегий и шаблонов решений
        - поиск подходящего навыка по задаче
        - повторное использование проверенных навыков
        - обучение новым навыкам (из опыта, рефлексии, документации)
        - композиция навыков: объединение нескольких в один
        - оценка эффективности навыков по статистике

    Используется:
        - Cognitive Core (Слой 3)        — выбор стратегии для задачи
        - Self-Improvement (Слой 12)     — добавление улучшенных навыков
        - Reflection System (Слой 10)    — обновление навыков по итогам
        - Agent System (Слой 4)          — специализированные навыки агентов
    """

    # Путь к файлу персистентности (рядом с остальной памятью агента)
    _PERSIST_PATH = pathlib.Path('.agent_memory') / 'skills.json'

    def __init__(self, cognitive_core=None, knowledge_system=None, monitoring=None):
        self.cognitive_core = cognitive_core
        self.knowledge = knowledge_system
        self.monitoring = monitoring

        self._skills: dict[str, Skill] = {}
        self._initializing = True
        self._load_defaults()       # сначала дефолты
        self._initializing = False
        self._load_from_disk()      # затем перезаписываем/дополняем сохранёнными

    # ── Регистрация навыков ───────────────────────────────────────────────────

    def register(self, name: str, description: str, strategy: str,
                 tags: list | None = None, examples: list | None = None) -> Skill:
        """Регистрирует новый навык в библиотеке."""
        skill = Skill(name, description, strategy, tags=tags, examples=examples)
        self._skills[name] = skill
        if self.knowledge:
            self.knowledge.store_long_term(
                f"skill:{name}", strategy, source='skill_library',
            )
        self._save()
        self._log(f"Навык зарегистрирован: '{name}'")
        return skill

    def update(self, name: str, strategy: str | None = None, description: str | None = None,
               tags: list | None = None):
        """Обновляет существующий навык."""
        skill = self._skills.get(name)
        if not skill:
            raise KeyError(f"Навык '{name}' не найден")
        if strategy:
            skill.strategy = strategy
        if description:
            skill.description = description
        if tags:
            skill.tags = tags
        skill.updated_at = time.time()
        self._save()
        self._log(f"Навык обновлён: '{name}'")

    def remove(self, name: str):
        """Удаляет навык из библиотеки."""
        self._skills.pop(name, None)
        self._save()

    # ── Поиск навыков ─────────────────────────────────────────────────────────

    # Веса компонентов relevance score (в сумме = 1.0)
    _SCORE_WEIGHTS = {
        'name':        0.35,   # совпадение слов задачи с именем навыка
        'tags':        0.30,   # покрытие тегов навыка словами задачи
        'description': 0.20,   # Jaccard-сходство значимых слов
        'success':     0.10,   # реальный success_rate навыка
        'frequency':   0.05,   # частота использования (log-нормализованная)
    }

    def find(self, task: str, top_k: int = 3) -> list[Skill]:
        """
        Находит наиболее подходящие навыки для задачи.

        Скоринг строится из 5 нормализованных компонентов [0, 1]:
            name        — пересечение слов задачи и имени навыка (Jaccard)
            tags        — доля тегов навыка, встречающихся в задаче
            description — Jaccard-сходство значимых слов задачи и описания
            success     — реальный success_rate (из накопленной статистики)
            frequency   — log-нормализованное число использований

        Итоговый score = взвешенная сумма компонентов.
        Навыки без совпадений по name, tags И description не попадают в результат.
        """
        task_words = self._significant_words(task)
        if not task_words:
            return []

        scored: list[tuple[float, Skill]] = []

        # Максимальное число использований среди всех навыков (для нормализации)
        max_uses = max((s.use_count for s in self._skills.values()), default=1)
        max_uses = max(max_uses, 1)

        for skill in self._skills.values():
            # ── Компонент 1: совпадение с именем навыка ───────────────────────
            name_words = self._significant_words(skill.name)
            name_score = self._jaccard(task_words, name_words)

            # ── Компонент 2: покрытие тегов ───────────────────────────────────
            if skill.tags:
                tag_hits = sum(
                    1 for tag in skill.tags
                    if tag.lower() in task.lower()
                    or any(w in self._significant_words(tag) for w in task_words)
                )
                tag_score = tag_hits / len(skill.tags)
            else:
                tag_score = 0.0

            # ── Компонент 3: Jaccard по словам описания ────────────────────────
            desc_words = self._significant_words(skill.description)
            desc_score = self._jaccard(task_words, desc_words)

            # ── Компонент 4: реальный success_rate ────────────────────────────
            success_score = skill.success_rate if skill.success_rate is not None else 0.5

            # ── Компонент 5: частота использования (log-нормализованная) ──────
            import math
            freq_score = math.log1p(skill.use_count) / math.log1p(max_uses)

            # ── Итоговый взвешенный score ──────────────────────────────────────
            w = self._SCORE_WEIGHTS
            total = (name_score   * w['name'] +
                     tag_score    * w['tags'] +
                     desc_score   * w['description'] +
                     success_score * w['success'] +
                     freq_score   * w['frequency'])

            # Порог: хотя бы один сигнал релевантности по содержанию
            if name_score > 0 or tag_score > 0 or desc_score > 0:
                scored.append((round(total, 4), skill))

        scored.sort(key=lambda x: -x[0])
        return [s for _, s in scored[:top_k]]

    @staticmethod
    def _significant_words(text: str) -> set[str]:
        """Возвращает множество значимых слов (длина > 3, нижний регистр)."""
        return {w.lower() for w in text.split() if len(w) > 3}

    @staticmethod
    def _jaccard(a: set, b: set) -> float:
        """Jaccard similarity между двумя множествами слов."""
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union

    def find_by_tag(self, tag: str) -> list[Skill]:
        """Возвращает все навыки с указанным тегом."""
        return [s for s in self._skills.values() if tag.lower() in s.tags]

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_all(self) -> list[dict]:
        return [s.to_dict() for s in self._skills.values()]

    def get_training_candidates(self, limit: int | None = None) -> list[Skill]:
        """Возвращает навыки, которые в первую очередь стоит тренировать."""
        skills = list(self._skills.values())
        skills.sort(
            key=lambda s: (
                s.use_count > 0,
                s.use_count,
                1.0 if s.success_rate is None else s.success_rate,
                s.updated_at,
                s.name,
            )
        )
        return skills[:limit] if limit is not None else skills

    # ── Применение навыка ─────────────────────────────────────────────────────

    def apply(self, name: str, task: str, success: bool = True) -> str | None:
        """
        Применяет навык к задаче через Cognitive Core.
        Записывает статистику использования.
        """
        skill = self._skills.get(name)
        if not skill:
            return None

        skill.record_use(success)

        if not self.cognitive_core:
            return skill.strategy

        result = self.cognitive_core.reasoning(
            f"Применяй следующий навык для решения задачи:\n\n"
            f"Навык: {skill.name}\n"
            f"Стратегия: {skill.strategy}\n\n"
            f"Задача: {task}"
        )
        self._log(f"Навык '{name}' применён к задаче")
        return result

    # ── Обучение новому навыку ────────────────────────────────────────────────

    def learn_from_experience(self, task: str, solution: str,
                              success: bool, tags: list | None = None) -> Skill | None:
        """
        Создаёт новый навык из опыта решения задачи.
        Используется Reflection/Self-Improvement для накопления библиотеки.
        """
        if not success:
            return None   # неудачный опыт не превращаем в навык

        if not self.cognitive_core:
            name = f"skill_{len(self._skills) + 1}"
            return self.register(name, task[:60], solution, tags=tags or [])

        # Cognitive Core формулирует навык из опыта
        raw = self.cognitive_core.reasoning(
            f"На основе успешного опыта сформулируй навык (стратегию):\n\n"
            f"Задача: {task}\n"
            f"Решение: {solution}\n\n"
            f"Ответь строго в формате:\n"
            f"НАЗВАНИЕ: <короткое имя навыка>\n"
            f"ОПИСАНИЕ: <когда применять>\n"
            f"СТРАТЕГИЯ: <пошаговая стратегия>"
        )
        lines = {line.split(':', 1)[0].strip(): line.split(':', 1)[1].strip()
                 for line in str(raw).splitlines() if ':' in line}

        name = lines.get('НАЗВАНИЕ', f"learned_{int(time.time())}")
        description = lines.get('ОПИСАНИЕ', task[:100])
        strategy = lines.get('СТРАТЕГИЯ', solution[:500])

        skill = self.register(name, description, strategy, tags=tags or ['learned'])
        skill.record_use(True)
        self._log(f"Новый навык обучен из опыта: '{name}'")
        return skill

    # ── Композиция навыков ────────────────────────────────────────────────────

    def compose(self, skill_names: list[str], name: str,
                description: str | None = None) -> Skill:
        """
        Объединяет несколько навыков в один составной навык.

        Args:
            skill_names — список имён навыков в порядке применения
            name        — имя нового составного навыка
        """
        parts = []
        all_tags = []
        for sname in skill_names:
            skill = self._skills.get(sname)
            if skill:
                parts.append(f"[{skill.name}]: {skill.strategy}")
                all_tags.extend(skill.tags)

        combined_strategy = '\n---\n'.join(parts)
        desc = description or f"Составной навык: {' + '.join(skill_names)}"
        composed = self.register(name, desc, combined_strategy,
                                 tags=list(set(all_tags)) + ['composed'])
        self._log(f"Составной навык создан: '{name}' из {skill_names}")
        return composed

    # ── Статистика ────────────────────────────────────────────────────────────

    def top_skills(self, n: int = 5) -> list[dict]:
        """Возвращает самые используемые навыки."""
        return sorted(
            [s.to_dict() for s in self._skills.values()],
            key=lambda x: x['use_count'],
            reverse=True,
        )[:n]

    def summary(self) -> dict:
        skills = list(self._skills.values())
        return {
            'total': len(skills),
            'by_tag': self._count_by_tag(),
            'avg_success_rate': round(
                sum(s.success_rate for s in skills if s.success_rate) /
                max(1, sum(1 for s in skills if s.success_rate)), 2
            ),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _count_by_tag(self) -> dict:
        from collections import Counter
        all_tags = [tag for s in self._skills.values() for tag in s.tags]
        return dict(Counter(all_tags))

    def _load_defaults(self):
        """Предзагрузка базовых навыков из архитектуры."""
        defaults = [
            # ── 1. ИИ и машинное обучение ─────────────────────────────────────
            ('research', 'Поиск информации', ['research'],
             'SEARCH:<тема> запрос в DuckDuckGo → READ: страницы со свежими результатами → '
             'извлеки ключевые факты → синтезируй ответ → WRITE:outputs/<тема>_research.txt'),

            # ── 2. Написание контента ─────────────────────────────────────────
            ('write_article', 'Написание статьи или материала', ['writing', 'content'],
             '1) Получи тему и целевую аудиторию. '
             '2) SEARCH:<тема> — исследуй 3-5 источников. '
             '3) Составь структуру: заголовок, введение, 3-5 разделов, заключение. '
             '4) Напиши текст через LLM (2000-5000 слов для лонгрида). '
             '5) WRITE:outputs/article_<slug>.txt — сохрани результат.'),

            # ── 3. Копирайтинг ────────────────────────────────────────────────
            ('copywriting', 'Коммерческий копирайтинг', ['writing', 'marketing'],
             '1) Определи продукт, аудиторию, цель (продажа/лид/клик). '
             '2) Используй формулу AIDA или PAS. '
             '3) Напиши заголовок (USP), подзаголовок, тело, CTA. '
             '4) Проверь через LLM на убедительность. '
             '5) WRITE:outputs/copy_<product>.txt'),

            # ── 4. Редактирование и корректура ────────────────────────────────
            ('proofread', 'Редактирование и корректура текста', ['writing', 'editing'],
             '1) READ:<файл> — прочитай исходный текст. '
             '2) Через LLM: исправь грамматику, орфографию, пунктуацию, стиль. '
             '3) Проверь логику, связность абзацев, повторы. '
             '4) WRITE:outputs/<исходный_файл>_edited.txt — сохрани исправленную версию. '
             '5) Выведи список изменений.'),

            # ── 5. Перевод ────────────────────────────────────────────────────
            ('translate', 'Перевод текста между языками', ['translation'],
             '1) READ:<файл> или получи текст напрямую. '
             '2) Определи исходный язык автоматически. '
             '3) TRANSLATE:<текст> target=<язык> через TranslateTool или LLM. '
             '4) Проверь через back-translation на точность. '
             '5) WRITE:outputs/translated_<lang>.txt'),

            # ── 6. SEO-контент ────────────────────────────────────────────────
            ('seo_content', 'Написание SEO-оптимизированного контента', ['writing', 'seo', 'marketing'],
             '1) SEARCH:<ключевые слова> site:google.com — найди топ-10 конкурентов. '
             '2) Извлеки LSI-ключевые слова из результатов поиска. '
             '3) Напиши текст: H1+H2 с ключевыми словами, meta-description, 1500+ слов. '
             '4) Плотность ключевых слов 1-2%, нет переспама. '
             '5) WRITE:outputs/seo_<slug>.txt'),

            # ── 7. Исследование рынка ─────────────────────────────────────────
            ('market_research', 'Исследование рынка и конкурентов', ['research', 'marketing'],
             '1) SEARCH:"<ниша> market size 2025 2026" — найди данные о рынке. '
             '2) SEARCH:"top competitors <ниша>" — список конкурентов. '
             '3) Для каждого конкурента: READ:их сайт → извлеки цены, USP, аудиторию. '
             '4) Сформируй SWOT-анализ и выводы через LLM. '
             '5) WRITE:outputs/market_research_<ниша>.txt'),

            # ── 8. Анализ данных ──────────────────────────────────────────────
            ('analyse_data', 'Анализ данных (CSV/Excel/JSON)', ['analysis', 'data'],
             '1) READ:<файл данных> через FileSystemTool или SpreadsheetTool. '
             '2) PYTHON: import pandas as pd → df = pd.read_csv/json/excel → df.describe(). '
             '3) Найди пустые значения, выбросы, распределения. '
             '4) PYTHON: построй сводные таблицы и корреляции. '
             '5) Сформулируй выводы через LLM. '
             '6) WRITE:outputs/analysis_report.txt'),

            # ── 9. ETL / Извлечение данных ────────────────────────────────────
            ('extract_data', 'Извлечение и трансформация данных (ETL)', ['data', 'etl'],
             '1) Определи источник: URL / файл / API. '
             '2) READ:URL через WebCrawler или HTTP:<url> через HTTPClientTool. '
             '3) PYTHON: распарси HTML (BeautifulSoup) или JSON/XML. '
             '4) Очисти: убери дубли, нормализуй форматы дат/чисел. '
             '5) WRITE:outputs/extracted_<источник>.csv — сохрани в CSV.'),

            # ── 10. Визуализация данных ───────────────────────────────────────
            ('visualize_data', 'Создание графиков и диаграмм', ['data', 'viz'],
             '1) READ:<файл данных> → загрузи через pandas. '
             '2) PYTHON: import matplotlib.pyplot as plt / seaborn. '
             '3) Выбери тип: bar/line/pie/scatter по задаче. '
             '4) Добавь заголовок, подписи осей, легенду. '
             '5) plt.savefig("outputs/chart_<название>.png") → сохрани.'),

            # ── 11. Генерация PDF-отчёта ──────────────────────────────────────
            ('generate_report', 'Генерация PDF-отчёта', ['reporting', 'writing'],
             '1) Собери данные: READ:outputs/*.txt — нужные файлы. '
             '2) Структурируй через LLM: резюме, основные разделы, выводы. '
             '3) PDF:<содержимое> через PDFGeneratorTool (reportlab). '
             '4) WRITE:outputs/report_<дата>.pdf — сохрани отчёт. '
             '5) Выведи путь к файлу.'),

            # ── 12. Написание кода ────────────────────────────────────────────
            ('write_code', 'Написание и запуск кода', ['coding'],
             '1) Пойми требования: язык, входные/выходные данные, ограничения. '
             '2) Напиши реализацию через LLM. '
             '3) PYTHON:<код> — запусти через PythonRuntimeTool. '
             '4) Если ошибка — исправь и перезапусти (до 3 попыток). '
             '5) WRITE:outputs/<имя>.py — сохрани финальный код.'),

            # ── 13. Ревью кода ────────────────────────────────────────────────
            ('code_review', 'Ревью и анализ кода', ['coding', 'analysis'],
             '1) READ:<файл.py> — прочитай код. '
             '2) CodeAnalyzerTool: проверь сложность, дубли, антипаттерны. '
             '3) LLM: найди баги, уязвимости (OWASP Top 10), нарушения стиля PEP8. '
             '4) Составь список: критично / важно / рекомендация. '
             '5) WRITE:outputs/code_review_<файл>.txt'),

            # ── 14. Отладка кода ──────────────────────────────────────────────
            ('debug', 'Отладка кода', ['coding', 'debugging'],
             '1) Воспроизведи ошибку: PYTHON:<код с ошибкой>. '
             '2) Прочитай traceback — найди строку и тип ошибки. '
             '3) Изолируй минимальный воспроизводящий пример. '
             '4) Исправь через LLM → перезапусти → проверь. '
             '5) WRITE:outputs/debug_log.txt — задокументируй решение.'),

            # ── 15. Маркетинговая стратегия ───────────────────────────────────
            ('marketing_strategy', 'Разработка маркетинговой стратегии', ['marketing'],
             '1) Проанализируй продукт: целевая аудитория, боли, USP. '
             '2) SEARCH:"<ниша> marketing strategy 2026" — исследуй тренды. '
             '3) Выбери каналы: SEO/контент/соцсети/email/платный трафик. '
             '4) Составь контент-план на 30 дней через LLM. '
             '5) WRITE:outputs/marketing_strategy_<продукт>.txt'),

            # ── 16. Написание email ───────────────────────────────────────────
            ('write_email', 'Написание профессионального email', ['writing', 'communication'],
             '1) Определи цель письма и получателя. '
             '2) Выбери тон: деловой / дружественный / продающий. '
             '3) Структура: тема (subject), приветствие, суть, CTA, подпись. '
             '4) LLM: напиши 150-300 слов, без воды. '
             '5) WRITE:outputs/email_draft_<тема>.txt'),

            # ── 17. Суммаризация документа ────────────────────────────────────
            ('summarize', 'Суммаризация длинного документа', ['writing', 'analysis'],
             '1) READ:<файл> — прочитай документ. '
             '2) Если > 4000 слов: разбей на чанки по 2000 слов. '
             '3) LLM: summarize каждый чанк → объедини → итоговое резюме. '
             '4) Формат вывода: ключевые тезисы (5-10 пунктов) + 1 абзац. '
             '5) WRITE:outputs/summary_<документ>.txt'),

            # ── 18. Работа с таблицами ────────────────────────────────────────
            ('spreadsheet', 'Создание и обработка таблиц Excel/CSV', ['data', 'spreadsheet'],
             '1) Определи структуру: колонки, типы данных, формулы. '
             '2) PYTHON: import openpyxl / pandas → создай DataFrame. '
             '3) Заполни данными, добавь формулы и форматирование. '
             '4) df.to_excel("outputs/<имя>.xlsx") или to_csv. '
             '5) Выведи preview первых 10 строк.'),

            # ── 19. Планирование задачи ───────────────────────────────────────
            ('plan_task', 'Планирование и декомпозиция задачи', ['planning'],
             '1) Определи конечную цель (SMART). '
             '2) Декомпозируй на подзадачи (не более 7 шагов). '
             '3) Для каждой: оцени время, зависимости, риски. '
             '4) Расставь приоритеты (MoSCoW). '
             '5) WRITE:outputs/plan_<задача>.txt — сохрани план.'),

            # ── 20. Поиск заказов Upwork ──────────────────────────────────────
            ('upwork_jobs', 'Поиск заказов на Upwork', ['freelance', 'upwork'],
             'ПОИСК ЗАКАЗОВ UPWORK: '
             '1) SEARCH:site:upwork.com/jobs "AI Automation" OR "Content Writing" OR '
             '"Data Analysis" OR "Translation" OR "Copywriting" OR "Market Research" '
             '2) Для каждого результата сформируй СТРОГО структурированный блок: '
             'Title: <название>; Budget: <бюджет или N/A>; Link: <https://...>; '
             'Posted: <дата>; Summary: <1-2 предложения>. '
             '3) Отфильтруй заказы старше 7 дней. '
             '4) Отсортируй по бюджету (высокий сначала). '
             '5) Перед WRITE проверь формат: каждый блок должен содержать минимум поля Link и Budget/Posted. '
             '6) WRITE:outputs/upwork_jobs.txt — сохрани ТОЛЬКО структурированные блоки вакансий, '
             'без DSL-команд, без markdown-кода, без фраз типа "SEARCH:"/"WRITE:"/"CONTENT:". '
             'Повторяй каждый цикл для отслеживания новых предложений.'),
        ]
        for name, desc, tags, strategy in defaults:
            self.register(name, desc, strategy, tags=tags)

    def _save(self):
        """Сохраняет все навыки на диск в JSON."""
        if getattr(self, '_initializing', False):
            return
        try:
            self._PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for name, skill in self._skills.items():
                data[name] = {
                    'description': skill.description,
                    'strategy': skill.strategy,
                    'tags': skill.tags,
                    'examples': skill.examples,
                    'use_count': skill.use_count,
                    'success_count': skill.success_count,
                    'created_at': skill.created_at,
                    'updated_at': skill.updated_at,
                }
            tmp = self._PERSIST_PATH.with_suffix('.tmp')
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self._PERSIST_PATH)
        except Exception as exc:
            self._log(f"Ошибка сохранения навыков: {exc}")

    def _load_from_disk(self):
        """Загружает навыки с диска; при конфликте имён — обновляет существующий навык."""
        if not self._PERSIST_PATH.exists():
            return
        try:
            data = json.loads(self._PERSIST_PATH.read_text(encoding='utf-8'))
            loaded = 0
            for name, d in data.items():
                skill = self._skills.get(name)
                if skill is None:
                    skill = Skill(
                        name=name,
                        description=d.get('description', ''),
                        strategy=d.get('strategy', ''),
                        tags=d.get('tags', []),
                        examples=d.get('examples', []),
                    )
                    self._skills[name] = skill
                else:
                    # Обновляем только если диск новее
                    if d.get('updated_at', 0) > skill.updated_at:
                        skill.description = d.get('description', skill.description)
                        skill.strategy = d.get('strategy', skill.strategy)
                        skill.tags = d.get('tags', skill.tags)
                skill.use_count = d.get('use_count', skill.use_count)
                skill.success_count = d.get('success_count', skill.success_count)
                skill.created_at = d.get('created_at', skill.created_at)
                skill.updated_at = d.get('updated_at', skill.updated_at)
                loaded += 1
            self._log(f"Загружено {loaded} навыков с диска ({self._PERSIST_PATH})")
        except Exception as exc:
            self._log(f"Ошибка загрузки навыков: {exc}")

    def _log(self, message: str):
        if self.monitoring:
            self.monitoring.info(message, source='skill_library')
        else:
            print(f"[SkillLibrary] {message}")

    def export_state(self) -> dict:
        """Возвращает полное состояние для персистентности."""
        data = {}
        for name, skill in self._skills.items():
            data[name] = {
                "name": skill.name,
                "description": getattr(skill, 'description', ''),
                "strategy": getattr(skill, 'strategy', ''),
                "tags": list(getattr(skill, 'tags', [])),
                "use_count": getattr(skill, 'use_count', 0),
                "success_count": getattr(skill, 'success_count', 0),
            }
        return data

    def import_state(self, data: dict):
        """Восстанавливает состояние из персистентного хранилища."""
        for name, sd in data.items():
            if name in self._skills:
                skill = self._skills[name]
                if hasattr(skill, 'use_count'):
                    skill.use_count = sd.get("use_count", 0)
                if hasattr(skill, 'success_count'):
                    skill.success_count = sd.get("success_count", 0)
                if hasattr(skill, 'strategy') and sd.get("strategy"):
                    skill.strategy = sd["strategy"]
