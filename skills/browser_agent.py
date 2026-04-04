# Browser Agent — автономный агент с браузером
#
# Что умеет:
#   - Открыть ЛЮБОЙ сайт в фоновом режиме (headless)
#   - Прочитать страницу, понять что там написано
#   - Перейти по ссылкам, листать дальше
#   - Через LLM решить: это важно для Андрея или нет
#   - Найти на Upwork вакансию → прочитать → прислать сообщение "Я нашёл вакансию, вот что надо"
#   - Найти любую другую информацию по запросу
#
# Принцип работы:
#   1. Получает задачу (строку): "найди вакансии Python на Upwork"
#   2. LLM составляет план: какой URL открыть, что искать, как понять результат
#   3. Агент выполняет шаги: navigate → read → analyze → navigate deeper → ...
#   4. Когда LLM решает что цель достигнута — отправляет результат в Telegram
#
# Запускается из autonomous_loop или по запросу через cognitive_core ("зайди на сайт...")

from __future__ import annotations

import json
import time
from typing import Any

from skills.job_hunter import ANDREY_PROFILE


# ── Константы ─────────────────────────────────────────────────────────────────

# Максимум шагов за одну задачу (защита от бесконечных циклов)
_MAX_STEPS = 15

# Максимум символов текста страницы передаём в LLM
_PAGE_TEXT_LIMIT = 4000

# Upwork поиск вакансий
_UPWORK_SEARCH = "https://www.upwork.com/nx/search/jobs/?q={query}&sort=recency"

# Белый список доменов — агент ходит ТОЛЬКО на эти сайты
# Любой URL от LLM, не входящий в этот список, отклоняется
_ALLOWED_DOMAINS = {
    'upwork.com',
    'www.upwork.com',
    'hh.ru',
    'www.hh.ru',
    'linkedin.com',
    'www.linkedin.com',
    'freelancer.com',
    'www.freelancer.com',
    'habr.com',
    'career.habr.com',
    'github.com',
    'www.github.com',
    'stackoverflow.com',
    'stackoverflow.jobs',
    'remoteok.com',
    'remoteok.io',
    'weworkremotely.com',
    'toptal.com',
    'www.toptal.com',
    'guru.com',
    'www.guru.com',
}

# Ключевые слова для автоматического выбора URL по задаче (без LLM)
_TASK_URL_MAP = [
    (['upwork',  'апворк'],                   _UPWORK_SEARCH),
    (['hh.ru',   'hh ', 'hh,', 'headhunter'], 'https://hh.ru/search/vacancy?text={query}&area=1&only_with_salary=false'),
    (['linkedin', 'линкедин'],                'https://www.linkedin.com/jobs/search/?keywords={query}'),
    (['freelancer.com', 'фрилансер'],         'https://www.freelancer.com/jobs/{query}/'),
    (['github',  'гитхаб'],                   'https://github.com/search?q={query}&type=repositories'),
]


class BrowserAgent:
    """
    Автономный агент, который управляет браузером для выполнения задач.

    Примеры задач:
      "Найди свежие вакансии Python automation на Upwork"
      "Зайди на hh.ru и найди вакансии AI с удалённой работой"
      "Проверь upwork.com/jobs — что за вакансии сейчас есть"
      "Открой эту вакансию и расскажи что от меня требуется: <url>"
    """

    def __init__(self, browser_tool=None, llm=None,
                 telegram_bot=None, telegram_chat_id=None, monitoring=None,
                 storage_state_path: str | None = None):
        self.llm = llm
        self.telegram_bot = telegram_bot
        self.telegram_chat_id = telegram_chat_id
        self.monitoring = monitoring
        self._browser_tool = browser_tool
        self.storage_state_path = storage_state_path or 'state/browser_storage.json'

    # ── Публичные методы ──────────────────────────────────────────────────────

    def browse(self, task: str, headless: bool = True) -> str:
        """
        Выполняет задачу через браузер.
        Возвращает итоговый ответ (текст для пользователя).
        headless=False для визуального debug-режима.
        """
        self._log(f"[browser_agent] Задача: {task}")

        browser = self._get_browser(headless=headless)
        try:
            result = self._execute_task(browser, task)
        except Exception as e:
            self._log(f"[browser_agent] Ошибка: {e}", level='error')
            result = f"Ошибка при выполнении задачи: {e}"
        finally:
            try:
                browser.close()
            except Exception:
                pass

        return result

    def read_page(self, url: str, question: str = '') -> str:
        """
        Читает конкретную страницу и отвечает на вопрос о ней.
        Если question пустой — возвращает краткое содержание.
        """
        browser = self._get_browser(headless=True)
        try:
            page = browser.navigate(url, wait_until='domcontentloaded')
            if not page.success:
                return f"Не удалось открыть страницу: {url}"

            text = page.text[:_PAGE_TEXT_LIMIT]
            if not question:
                return self._summarize_page(page.title, url, text)
            else:
                return self._answer_about_page(page.title, url, text, question)
        finally:
            try:
                browser.close()
            except Exception:
                pass

    def hunt_jobs_browser(self, query: str = 'python automation AI') -> list[dict]:
        """
        Ищет вакансии на Upwork через браузер (не RSS).
        Читает страницу поиска → заходит в каждую вакансию → анализирует.
        Возвращает список подходящих вакансий.
        """
        browser = self._get_browser(headless=True)
        found = []
        try:
            url = _UPWORK_SEARCH.format(query=query.replace(' ', '+'))
            self._log(f"[browser_agent] Открываю поиск: {url}")

            page = browser.navigate(url, wait_until='networkidle')
            if not page.success:
                self._log("[browser_agent] Не удалось открыть поиск Upwork", level='warning')
                return []

            # Извлекаем ссылки на вакансии с поисковой страницы
            job_links = self._extract_job_links(browser, page)
            self._log(f"[browser_agent] Найдено ссылок на вакансии: {len(job_links)}")

            # Читаем каждую вакансию
            for link in job_links[:8]:   # читаем первые 8
                time.sleep(1.5)
                job = self._read_job_page(browser, link)
                if job:
                    fit, reason = self._evaluate_job(job)
                    if fit:
                        found.append(job)
                        self._notify_job_found(job)
                        self._log(f"[browser_agent] ✅ {job.get('title', '')[:60]}")
                    else:
                        self._log(f"[browser_agent] ❌ {job.get('title', '')[:50]} — {reason}")
        finally:
            try:
                browser.close()
            except Exception:
                pass

        return found

    # ── Основная логика задачи ────────────────────────────────────────────────

    def _execute_task(self, browser, task: str) -> str:
        """
        LLM-driven браузер: составляет план и выполняет шаги.
        На каждом шаге LLM смотрит что на странице и решает что делать дальше.
        """
        if not self.llm:
            # Без LLM — просто ищем на Upwork по ключевым словам из задачи
            return self._simple_search(browser, task)

        # Шаг 0: LLM составляет начальный URL
        start_url = self._plan_start_url(task)
        self._log(f"[browser_agent] Начальный URL: {start_url}")

        browser.navigate(start_url, wait_until='domcontentloaded')
        time.sleep(1.5)

        history: list[dict] = []   # история шагов для контекста LLM

        for step in range(_MAX_STEPS):
            page_text = (browser.get_text() or '')[:_PAGE_TEXT_LIMIT]
            current_url = browser.current_url

            # ── CAPTCHA detection ──
            captcha = browser.detect_captcha()
            if captcha.get('detected'):
                cap_type = captcha.get('type', 'unknown')
                self._log(f"[browser_agent] CAPTCHA detected: {cap_type}", level='warning')
                self._send_telegram(
                    f"⚠️ <b>CAPTCHA обнаружена</b>\n"
                    f"Тип: {cap_type}\nURL: {current_url}\n"
                    f"Задача приостановлена — требуется ручное решение."
                )
                history.append({
                    'step': step + 1,
                    'url': current_url,
                    'action': 'captcha_blocked',
                    'note': f'CAPTCHA: {cap_type}',
                })
                break

            # LLM анализирует текущую страницу и решает что делать
            decision = self._think(task, current_url, page_text, history, step)

            self._log(f"[browser_agent] Шаг {step+1}: action={decision.get('action')}")
            history.append({
                'step': step + 1,
                'url': current_url,
                'action': decision.get('action'),
                'note': decision.get('note', ''),
            })

            action = decision.get('action', 'done')

            if action == 'done':
                # Задача выполнена — формируем ответ
                answer = decision.get('answer', '')
                if not answer:
                    answer = self._summarize_findings(task, history, page_text)
                # Отправляем результат в Telegram
                self._notify_result(task, answer)
                return answer

            elif action == 'navigate':
                next_url = decision.get('url', '')
                if next_url and self._is_allowed_url(next_url):
                    browser.navigate(next_url, wait_until='domcontentloaded')
                    time.sleep(1.5)
                elif next_url:
                    self._log(
                        f"[browser_agent] LLM предложил URL вне белого списка: {next_url[:80]} — пропускаем",
                        level='warning'
                    )

            elif action == 'click':
                selector = decision.get('selector', '')
                text = decision.get('text', '')
                if text:
                    # Ищем ссылку по тексту через JS
                    browser.evaluate(
                        f'() => {{ const els = [...document.querySelectorAll("a,button")];'
                        f'const el = els.find(e => e.innerText.includes({json.dumps(text)}));'
                        f'if(el){{ el.click(); return true; }} return false; }}'
                    )
                    time.sleep(2)
                elif selector:
                    browser.click(selector)
                    time.sleep(2)

            elif action == 'fill':
                # Заполнение форм — логин, поиск, фильтры
                selector = decision.get('selector', '')
                value = decision.get('value', '')
                if selector and value:
                    browser.fill(selector, value)
                    time.sleep(0.5)
                    # Автоматический Enter если поле поиска
                    if decision.get('submit'):
                        browser.press(selector, 'Enter')
                        time.sleep(1.5)

            elif action == 'type':
                # Посимвольный ввод (для полей с autocomplete/JS)
                selector = decision.get('selector', '')
                value = decision.get('value', '')
                if selector and value and browser._page:
                    browser._page.locator(selector).first.type(value, delay=50)
                    time.sleep(0.5)

            elif action == 'select':
                # Выбор опции в dropdown
                selector = decision.get('selector', '')
                value = decision.get('value', '')
                label = decision.get('label', '')
                if selector:
                    browser.select_option(selector, value=value or None,
                                          label=label or None)
                    time.sleep(0.5)

            elif action == 'hover':
                selector = decision.get('selector', '')
                if selector:
                    browser.hover(selector)
                    time.sleep(0.5)

            elif action == 'upload':
                selector = decision.get('selector', '')
                filepath = decision.get('filepath', '')
                # SECURITY: ограничиваем upload только файлами из outputs/
                if selector and filepath:
                    abs_fp = os.path.realpath(os.path.abspath(filepath))
                    allowed_dir = os.path.realpath(os.path.join(
                        os.path.dirname(os.path.dirname(__file__)), 'outputs'))
                    if abs_fp.startswith(allowed_dir + os.sep):
                        browser.set_input_files(selector, filepath)
                        time.sleep(1)
                    else:
                        self._log(
                            f"[browser_agent] Upload заблокирован: {filepath} вне outputs/",
                            level='warning'
                        )

            elif action == 'screenshot':
                # LLM может запросить скриншот для debug
                browser.screenshot(path=f'outputs/browser_step_{step+1}.png')

            elif action == 'scroll':
                direction = decision.get('direction', 'down')
                amount = 800 if direction == 'down' else -800
                browser.scroll(0, amount)
                time.sleep(0.5)

            elif action == 'search':
                query = decision.get('query', task)
                search_url = _UPWORK_SEARCH.format(query=query.replace(' ', '+'))
                browser.navigate(search_url, wait_until='domcontentloaded')
                time.sleep(2)

            elif action == 'read_links':
                # Читаем ссылки с текущей страницы и выбираем лучшую для перехода
                links = browser.get_links()
                best_link = self._pick_best_link(task, links, page_text)
                if best_link:
                    browser.navigate(best_link, wait_until='domcontentloaded')
                    time.sleep(1.5)

            elif action == 'wait':
                selector = decision.get('selector', '')
                if selector:
                    browser.wait_for(selector, timeout=5000)

            elif action == 'press':
                selector = decision.get('selector', 'body')
                key = decision.get('key', 'Enter')
                browser.press(selector, key)
                time.sleep(0.5)

        # Превышен лимит шагов
        final_text = (browser.get_text() or '')[:_PAGE_TEXT_LIMIT]
        answer = self._summarize_findings(task, history, final_text)
        self._notify_result(task, answer)
        return answer

    # ── LLM-вызовы ───────────────────────────────────────────────────────────

    def _plan_start_url(self, task: str) -> str:
        """
        Определяет стартовый URL по задаче.
        НЕ доверяет LLM для выбора URL — LLM может галлюцинировать домены.
        Использует детерминированное сопоставление по ключевым словам + вайтлист.
        """
        task_lower = task.lower()
        keywords = self._extract_search_keywords(task)
        query = '+'.join(keywords[:4]) if keywords else task[:50].replace(' ', '+')

        # 1. Если в задаче явно указан URL — проверяем через вайтлист
        import re as _re
        url_match = _re.search(r'https?://[^\s]+', task)
        if url_match:
            candidate = url_match.group(0).rstrip('.,;)')
            if self._is_allowed_url(candidate):
                return candidate
            else:
                self._log(
                    f"[browser_agent] URL '{candidate}' не в белом списке — игнорируем",
                    level='warning'
                )

        # 2. Сопоставление по ключевым словам задачи
        for triggers, url_template in _TASK_URL_MAP:
            if any(t in task_lower for t in triggers):
                return url_template.format(query=query)

        # 3. По умолчанию — Upwork (основная площадка Андрея)
        return _UPWORK_SEARCH.format(query=query)

    def _is_allowed_url(self, url: str) -> bool:
        """Проверяет URL по белому списку доменов."""
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.lower()
            # Убираем порт если есть
            domain = domain.split(':')[0]
            return domain in _ALLOWED_DOMAINS
        except Exception:
            return False

    @staticmethod
    def _extract_search_keywords(task: str) -> list[str]:
        """Извлекает ключевые слова из задачи для поискового запроса."""
        import re as _re
        # Убираем служебные слова
        stopwords = {
            'найди', 'найти', 'поищи', 'зайди', 'открой', 'прочитай', 'посмотри',
            'на', 'в', 'и', 'по', 'для', 'через', 'браузер', 'браузере',
            'сайт', 'сайте', 'вакансии', 'вакансию', 'вакансий',
            'upwork', 'hh', 'linkedin', 'интернете', 'поиск',
            'find', 'search', 'open', 'go', 'read', 'look', 'for', 'on', 'the', 'at',
            'jobs', 'job', 'vacancy', 'vacancies',
        }
        words = _re.findall(r'[A-Za-zА-Яа-яЁё0-9_+#.-]{2,}', task.lower())
        return [w for w in words if w not in stopwords]

    def _think(self, task: str, current_url: str, page_text: str,
               history: list, step: int) -> dict:
        """
        LLM смотрит на страницу и решает следующий шаг.
        Возвращает dict с ключами: action, url/selector/text/query, note, answer
        """
        if not self.llm:
            return {'action': 'done', 'answer': page_text[:500]}

        history_str = '\n'.join(
            f"  Шаг {h['step']}: {h['action']} — {h['note']}"
            for h in history[-5:]   # последние 5 шагов
        )

        prompt = f"""You are controlling a web browser to complete a task.

Task: {task}

User profile: {ANDREY_PROFILE}

Current URL: {current_url}
Step: {step + 1}/{_MAX_STEPS}

Previous steps:
{history_str or '  (none yet)'}

Current page content (first {_PAGE_TEXT_LIMIT} chars):
{page_text}

---
Based on the page content, decide the NEXT action to complete the task.

Available actions:
- done         → task is complete, provide the answer
- navigate     → go to a specific URL (provide "url")
- click        → click a link/button (provide "text" to match, or "selector")
- fill         → fill a text field (provide "selector", "value"; set "submit":true to press Enter after)
- type         → type slowly into a field with autocomplete (provide "selector", "value")
- select       → choose dropdown option (provide "selector", "value" or "label")
- hover        → hover over element (provide "selector")
- upload       → upload a file (provide "selector", "filepath")
- press        → press a key (provide "selector", "key" e.g. "Enter", "Tab")
- scroll       → scroll page (provide "direction": "down" or "up")
- search       → search on Upwork (provide "query")
- read_links   → analyze all links on page and pick the best one to follow
- wait         → wait for element to appear (provide "selector")
- screenshot   → take debug screenshot

If you found a job vacancy and it's relevant to the profile, return action=done with a full answer in Russian.

Reply ONLY with valid JSON, no markdown:
{{"action": "...", "url": "...", "text": "...", "selector": "...", "value": "...", "label": "...", "query": "...", "key": "...", "direction": "...", "filepath": "...", "submit": false, "note": "one-line note in Russian", "answer": "full answer in Russian (only if action=done)"}}"""

        try:
            raw = self.llm.infer(prompt, max_tokens=400, temperature=0.2)
            # Извлекаем JSON из ответа
            import re
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            self._log(f"[browser_agent] LLM think error: {e}", level='error')
        return {'action': 'scroll', 'note': 'LLM error — scrolling'}

    def _summarize_page(self, title: str, url: str, text: str) -> str:
        """Краткое содержание страницы."""
        if not self.llm:
            return f"Страница: {title}\nURL: {url}\n\n{text[:1000]}"
        prompt = (
            f"URL: {url}\nTitle: {title}\n\nPage text:\n{text}\n\n"
            "Summarize this page in Russian in 3-5 sentences. What is it about?"
        )
        try:
            return self.llm.infer(prompt, max_tokens=300)
        except Exception:
            return f"{title}\n{text[:500]}"

    def _answer_about_page(self, title: str, url: str,
                            text: str, question: str) -> str:
        """Отвечает на вопрос о конкретной странице."""
        if not self.llm:
            return text[:1500]
        prompt = (
            f"URL: {url}\nTitle: {title}\n\nPage content:\n{text}\n\n"
            f"Question: {question}\n\n"
            "Answer the question based on the page content. Reply in Russian."
        )
        try:
            return self.llm.infer(prompt, max_tokens=500)
        except Exception:
            return text[:1500]

    def _summarize_findings(self, task: str, history: list, final_text: str) -> str:
        """Итоговый ответ по результатам всех шагов."""
        if not self.llm:
            return final_text[:1500]

        steps_str = '\n'.join(
            f"  {h['step']}. {h['url'][:60]} — {h['note']}"
            for h in history
        )
        prompt = (
            f"Task: {task}\n\nBrowsing history:\n{steps_str}\n\n"
            f"Final page content:\n{final_text[:2000]}\n\n"
            "Summarize what was found. Answer in Russian. "
            "If a job vacancy was found, describe: title, what's required, URL."
        )
        try:
            return self.llm.infer(prompt, max_tokens=600)
        except Exception:
            return final_text[:1000]

    def _evaluate_job(self, job: dict) -> tuple[bool, str]:
        """Оценивает подходит ли вакансия Андрею."""
        if not self.llm:
            return True, ""
        prompt = (
            f"{ANDREY_PROFILE}\n\n"
            f"Job title: {job.get('title', '')}\n"
            f"Description:\n{job.get('description', '')[:2000]}\n\n"
            "Does Andrey qualify? Reply: FIT: yes OR FIT: no — <reason in Russian, 10 words>"
        )
        try:
            answer = self.llm.infer(prompt, max_tokens=80, temperature=0.1)
            if 'fit: yes' in answer.lower():
                return True, ""
            reason = answer.split('—', 1)[-1].strip() if '—' in answer else answer
            return False, reason
        except Exception:
            return True, ""

    def _pick_best_link(self, task: str, links: list, page_text: str) -> str | None:
        """LLM выбирает лучшую ссылку для перехода."""
        if not links:
            return None
        if not self.llm:
            # Без LLM берём первую ссылку Upwork
            for link in links:
                if 'upwork.com/jobs' in link or 'upwork.com/freelance-jobs' in link:
                    return link
            return None

        links_str = '\n'.join(links[:20])
        prompt = (
            f"Task: {task}\n\n"
            f"Available links:\n{links_str}\n\n"
            "Which link is most relevant to the task? Reply with ONLY the URL."
        )
        try:
            url = self.llm.infer(prompt, max_tokens=100).strip()
            if url.startswith('http') and url in links:
                return url
        except Exception:
            pass
        return None

    # ── Вакансии ──────────────────────────────────────────────────────────────

    def _extract_job_links(self, browser, page) -> list[str]:
        """Извлекает ссылки на вакансии Upwork с поисковой страницы."""
        links = page.links or browser.get_links()
        job_links = [
            lnk for lnk in links
            if '/jobs/' in lnk and 'upwork.com' in lnk
            and '~' in lnk   # uid вакансии содержит ~
        ]
        return list(dict.fromkeys(job_links))   # дедупликация с сохранением порядка

    def _read_job_page(self, browser, url: str) -> dict | None:
        """Читает страницу вакансии и возвращает структурированные данные."""
        try:
            page = browser.navigate(url, wait_until='domcontentloaded')
            if not page.success:
                return None

            text = page.text[:_PAGE_TEXT_LIMIT]
            title = page.title or url.split('/')[-1]

            return {
                'title': title,
                'description': text,
                'url': url,
            }
        except Exception as e:
            self._log(f"[browser_agent] Ошибка чтения {url}: {e}", level='error')
            return None

    # ── Уведомления ───────────────────────────────────────────────────────────

    def _notify_job_found(self, job: dict):
        """Отправляет уведомление о найденной вакансии."""
        title = job.get('title', 'Вакансия')
        url = job.get('url', '')
        desc = job.get('description', '')[:800]

        msg = (
            f"🎯 <b>Нашёл вакансию!</b>\n\n"
            f"📌 <b>{title}</b>\n"
            f"🔗 {url}\n\n"
            f"<b>Что требуется:</b>\n{desc}"
        )
        self._send_telegram(msg)

    def _notify_result(self, task: str, answer: str):
        """Отправляет итоговый результат задачи."""
        msg = (
            f"🌐 <b>Выполнил задачу браузером</b>\n"
            f"<i>{task[:80]}</i>\n\n"
            f"{answer}"
        )
        self._send_telegram(msg)

    def _send_telegram(self, text: str):
        if self.telegram_bot and self.telegram_chat_id:
            try:
                # Telegram лимит 4096 символов
                if len(text) > 4000:
                    text = text[:4000] + '...'
                self.telegram_bot.send(self.telegram_chat_id, text)
            except Exception as e:
                self._log(f"[browser_agent] Telegram: {e}")

    # ── Без LLM ───────────────────────────────────────────────────────────────

    def _simple_search(self, browser, task: str) -> str:
        """Простой поиск без LLM — ищет на Upwork по словам из задачи."""
        url = _UPWORK_SEARCH.format(query=task[:60].replace(' ', '+'))
        page = browser.navigate(url, wait_until='domcontentloaded')
        if page.success:
            text = page.text[:2000]
            self._notify_result(task, f"Результаты поиска Upwork:\n{text}")
            return text
        return "Не удалось выполнить поиск."

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def _get_browser(self, headless: bool = True):
        """Возвращает браузер — переданный или создаёт новый."""
        from tools.browser_tool import BrowserTool
        if self._browser_tool is not None:
            return self._browser_tool
        return BrowserTool(
            headless=headless,
            timeout=30000,
            monitoring=self.monitoring,
            storage_state_path=self.storage_state_path,
        )

    def _log(self, msg: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                msg, source='browser_agent'
            )
        else:
            print(msg)
