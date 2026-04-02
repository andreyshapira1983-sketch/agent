# Upwork Portfolio Filler — автоматически заполняет форму добавления проекта
# Использует BrowserTool (Playwright) с ВИДИМЫМ браузером (headless=False)
#
# Upwork-форма это WIZARD с несколькими шагами:
#   Шаг 1: Название + описание проекта
#   → Next → Шаг 2: Роль + навыки (скиллы)
#   → Next → Шаг 3: Ссылка на проект + дата (опционально)
#   → Next: Preview → Preview страница
#   → Save → Проект сохранён
#
# Агент нажимает Next после каждого шага, делает скриншот, отправляет в Telegram.
# Если интерфейс Upwork поменялся — агент читает текст страницы и
# ищет кнопку Next/Save по тексту (text-based fallback).

from __future__ import annotations

import time
import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_UPWORK_ADD_URL = "https://www.upwork.com/freelancers/settings/portfolio/add"

# ── Шаг 1: Название и описание ────────────────────────────────────────────────
_S1_TITLE   = ['input[data-test="portfolio-project-title"]',
                'input[placeholder*="title" i]',
                'input[name*="title" i]']
_S1_DESC    = ['textarea[data-test="portfolio-project-description"]',
               'textarea[placeholder*="description" i]',
               'textarea[placeholder*="goal" i]',
               'textarea']

# ── Шаг 2: Роль и навыки ──────────────────────────────────────────────────────
_S2_ROLE    = ['input[data-test="portfolio-project-role"]',
               'input[placeholder*="role" i]',
               'input[name*="role" i]']
_S2_SKILLS  = ['input[data-test="portfolio-project-skills"]',
               'input[placeholder*="skill" i]',
               'input[data-qa*="skill" i]']

# ── Шаг 3: URL + даты (опционально) ───────────────────────────────────────────
_S3_URL     = ['input[data-test="portfolio-project-url"]',
               'input[placeholder*="url" i]',
               'input[type="url"]']

# ── Кнопки навигации ──────────────────────────────────────────────────────────
# Варианты кнопки "Далее/Next" — от точного к универсальному
_BTN_NEXT = [
    'button[data-test="btn-next-preview"]',
    'button[data-test*="next"]',
    'button[data-qa*="next"]',
    'button:has-text("Next: Preview")',
    'button:has-text("Next")',
    'button:has-text("Далее")',
    '[role="button"]:has-text("Next")',
    'button[type="submit"]',
]
_BTN_SAVE = [
    'button[data-test="btn-save"]',
    'button[data-test*="save"]',
    'button:has-text("Save")',
    'button:has-text("Publish")',
    'button:has-text("Add Project")',
    'button:has-text("Сохранить")',
]


class UpworkPortfolioFiller:
    """
    Открывает браузер и проходит всю wizard-форму Upwork Portfolio по шагам.
    Требует Playwright: pip install playwright && playwright install chromium
    """

    def __init__(self, browser_tool=None, monitoring=None,
                 telegram_bot=None, telegram_chat_id=None):
        self.monitoring = monitoring
        self.telegram_bot = telegram_bot
        self.telegram_chat_id = telegram_chat_id
        self._browser_tool = browser_tool

    # ── Публичный метод ───────────────────────────────────────────────────────

    def fill_project(self, project: dict) -> dict:
        """
        Заполняет форму одним проектом, проходя все шаги wizard.
        project = {title, role, description, skills: [...]}
        Возвращает {'success': bool, 'message': str, 'screenshot_b64': str|None}
        """
        try:
            from tools.browser_tool import BrowserTool
        except ImportError:
            return {'success': False, 'message': 'BrowserTool не найден.'}

        # Если браузер передан снаружи — используем его, иначе создаём новый
        # headless=False — пользователь видит что происходит
        browser = self._browser_tool
        if browser is None:
            browser = BrowserTool(
                headless=False,
                timeout=30000,
                monitoring=self.monitoring,
            )
        own_browser = self._browser_tool is None  # закрывать только если создали сами

        try:
            return self._run_wizard(browser, project)
        except Exception as e:
            self._log(f"[portfolio] Критическая ошибка: {e}", level='error')
            try:
                ss = browser.screenshot()
            except Exception:
                ss = None
            return {'success': False, 'message': str(e), 'screenshot_b64': ss}
        finally:
            # Закрываем только свой браузер; внешний оставляем владельцу
            if own_browser:
                try:
                    browser.close()
                except Exception:
                    pass

    def _run_wizard(self, browser, project: dict) -> dict:
        """Проходит все шаги формы Upwork по очереди."""
        title = project.get('title', '')[:70]
        role  = project.get('role', '')[:100]
        desc  = project.get('description', '')[:600]
        skills = project.get('skills', [])

        # 0. Открываем форму
        self._log("[portfolio] Открываю Upwork Add Portfolio...")
        browser.navigate(_UPWORK_ADD_URL, wait_until='domcontentloaded')
        time.sleep(2)

        # 0a. Проверка логина
        if not self._ensure_logged_in(browser):
            return {
                'success': False,
                'message': 'Время ожидания логина истекло. Войди в Upwork и попробуй снова.',
                'screenshot_b64': browser.screenshot(),
            }

        # Если после логина улетели с нужной страницы — заходим снова
        if 'portfolio/add' not in browser.current_url:
            browser.navigate(_UPWORK_ADD_URL, wait_until='domcontentloaded')
            time.sleep(2)

        step_results = []

        # ── ШАГ 1: Название + описание ────────────────────────────────────────
        self._log("[portfolio] Шаг 1: название и описание")
        ok1 = self._step_title_description(browser, title, desc)
        ss1 = browser.screenshot()
        self._notify_telegram(
            f"🟦 <b>Шаг 1/3 заполнен</b>\n📌 {title}",
            ss1
        )
        step_results.append(('step1', ok1))

        if not self._click_next(browser, step=1):
            return {
                'success': False,
                'message': 'Не нашёл кнопку Next после шага 1. Проверь браузер.',
                'screenshot_b64': browser.screenshot(),
            }
        self._wait_for_next_step(browser, current_selectors=_S1_TITLE)

        # ── ШАГ 2: Роль + навыки ──────────────────────────────────────────────
        self._log("[portfolio] Шаг 2: роль и навыки")
        ok2 = self._step_role_skills(browser, role, skills)
        ss2 = browser.screenshot()
        self._notify_telegram(
            f"🟩 <b>Шаг 2/3 заполнен</b>\n👤 {role} | {len(skills)} навыков",
            ss2
        )
        step_results.append(('step2', ok2))

        if not self._click_next(browser, step=2):
            # Шаг 2 может не существовать или уже на preview — пробуем Save
            self._log("[portfolio] Кнопка Next на шаге 2 не найдена — пробую Save")
        else:
            self._wait_for_next_step(browser, current_selectors=_S2_ROLE)

        # ── ШАГ 3: URL (опционально) ──────────────────────────────────────────
        # Проверяем — есть ли поле URL на текущей странице
        self._log("[portfolio] Шаг 3: проверяю поле URL...")
        if self._has_any(browser, _S3_URL, timeout=3000):
            ok3 = self._step_project_url(browser, project.get('project_url', ''))
            step_results.append(('step3_url', ok3))
            ss3 = browser.screenshot()
            self._notify_telegram("🟨 <b>Шаг 3/3 заполнен</b> (URL проекта)", ss3)
            self._click_next(browser, step=3)
            self._wait_for_next_step(browser, current_selectors=_S3_URL, timeout=5)
        else:
            self._log("[portfolio] Поле URL не найдено — пропускаю шаг 3")

        # ── PREVIEW / SAVE ────────────────────────────────────────────────────
        # Читаем страницу — что сейчас отображается
        page_text = browser.get_text()
        is_preview = 'preview' in browser.current_url.lower() or \
                     'preview' in (page_text or '').lower()[:200]

        ss_final = browser.screenshot()
        self._log(f"[portfolio] Финальная страница. URL: {browser.current_url}")

        if is_preview:
            # Нажимаем Save/Publish
            saved = self._click_any(browser, _BTN_SAVE)
            if saved:
                time.sleep(2)
                ss_saved = browser.screenshot()
                self._notify_telegram(
                    f"✅ <b>Проект сохранён на Upwork!</b>\n📌 {title}",
                    ss_saved
                )
                return {
                    'success': True,
                    'message': f'Проект "{title}" успешно добавлен в Upwork Portfolio.',
                    'screenshot_b64': ss_saved,
                }
            self._notify_telegram(
                f"👁 <b>Preview готов — нажми Save!</b>\n📌 {title}\n\n"
                "Зайди в браузер и нажми <b>Save / Add Project</b>.",
                ss_final
            )
            return {
                'success': True,
                'message': 'Форма заполнена. Открыт Preview — нажми Save в браузере.',
                'screenshot_b64': ss_final,
            }
        # Не дошли до preview — уведомляем пользователя
        self._notify_telegram(
            f"⚠️ <b>Форма заполнена, но нужна проверка</b>\n📌 {title}\n\n"
            "Зайди в браузер — проверь и нажми <b>Next: Preview → Save</b>.",
            ss_final
        )
        return {
            'success': True,
            'message': 'Форма заполнена. Проверь браузер и дожми до Save.',
            'screenshot_b64': ss_final,
        }

    # ── Шаги формы ───────────────────────────────────────────────────────────

    def _step_title_description(self, browser, title: str, desc: str) -> bool:
        ok_t = self._smart_fill(browser, _S1_TITLE, title)
        time.sleep(0.4)
        ok_d = self._smart_fill(browser, _S1_DESC, desc)
        time.sleep(0.4)
        return ok_t and ok_d

    def _step_role_skills(self, browser, role: str, skills: list) -> bool:
        ok_r = True
        if role:
            ok_r = self._smart_fill(browser, _S2_ROLE, role)
            time.sleep(0.4)

        ok_s = True
        for skill in skills[:5]:
            sel = self._find_selector(browser, _S2_SKILLS, timeout=2000)
            if sel:
                browser.fill(sel, skill)
                time.sleep(0.3)
                browser.press(sel, 'Enter')
                time.sleep(0.6)
            else:
                ok_s = False
                break
        return ok_r and ok_s

    def _step_project_url(self, browser, url: str) -> bool:
        if not url:
            return True  # поле пустым оставляем — это ок
        return self._smart_fill(browser, _S3_URL, url)

    # ── Навигация между шагами ────────────────────────────────────────────────

    def _click_next(self, browser, step: int) -> bool:
        """Нажимает кнопку Next/Далее. Пробует все известные варианты."""
        self._log(f"[portfolio] Нажимаю Next (шаг {step})...")
        result = self._click_any(browser, _BTN_NEXT)
        if result:
            time.sleep(1)
        return result

    def _wait_for_next_step(self, browser, current_selectors: list,
                             timeout: int = 10):
        """
        Ждёт перехода на следующий шаг wizard:
        — либо URL изменился
        — либо исчезли текущие поля (Upwork убирает DOM при шаге)
        — либо просто ждём timeout секунд
        """
        start_url = browser.current_url
        for _ in range(timeout * 2):  # проверяем каждые 0.5 сек
            time.sleep(0.5)
            new_url = browser.current_url
            if new_url != start_url:
                self._log(f"[portfolio] Страница сменилась: {new_url}")
                time.sleep(1)
                return
            # Если DOM изменился (поля исчезли) — тоже считаем переходом
            if not browser.wait_for(current_selectors[0], timeout=200):
                self._log("[portfolio] DOM обновился — новый шаг загружен")
                time.sleep(0.5)
                return
        self._log(f"[portfolio] Таймаут ожидания следующего шага ({timeout}с)")

    # ── Логин ─────────────────────────────────────────────────────────────────

    def _ensure_logged_in(self, browser) -> bool:
        """Если редирект на login — ждём пока пользователь войдёт (до 2 минут)."""
        cur = browser.current_url
        if 'login' not in cur and 'signup' not in cur:
            return True

        self._log("[portfolio] Требуется логин — жду пользователя (120 сек)")
        self._notify_telegram(
            "🔐 <b>Upwork Portfolio Filler</b>\n\n"
            "Браузер открыт. Войди в Upwork — после входа агент продолжит.\n\n"
            "⏳ Ожидание до 2 минут..."
        )
        for _ in range(24):   # 24 × 5 = 120 сек
            time.sleep(5)
            cur = browser.current_url
            if 'login' not in cur and 'signup' not in cur:
                return True
        return False

    # ── Утилиты ───────────────────────────────────────────────────────────────

    def _smart_fill(self, browser, selectors: list, text: str) -> bool:
        """Заполняет первое найденное поле из списка селекторов."""
        sel = self._find_selector(browser, selectors, timeout=4000)
        if not sel:
            self._log(f"[portfolio] Поле не найдено: {selectors[0]}...", level='warning')
            return False
        browser.fill(sel, '')   # очистка перед вводом
        time.sleep(0.1)
        return browser.fill(sel, text)

    def _find_selector(self, browser, selectors: list,
                        timeout: int = 3000) -> str | None:
        """Возвращает первый работающий селектор из списка."""
        for sel in selectors:
            if browser.wait_for(sel, timeout=timeout):
                return sel
            timeout = min(timeout, 1000)  # для последующих — быстрее
        return None

    def _has_any(self, browser, selectors: list, timeout: int = 2000) -> bool:
        """True если хотя бы один из селекторов присутствует на странице."""
        return self._find_selector(browser, selectors, timeout=timeout) is not None

    def _click_any(self, browser, selectors: list) -> bool:
        """Нажимает первую найденную кнопку из списка."""
        for sel in selectors:
            if browser.wait_for(sel, timeout=1500):
                if browser.click(sel):
                    return True
        return False

    # ── Telegram ──────────────────────────────────────────────────────────────

    def _notify_telegram(self, text: str, screenshot_b64: str | None = None):
        if not self.telegram_bot or not self.telegram_chat_id:
            return
        try:
            self.telegram_bot.send(self.telegram_chat_id, text)
            if screenshot_b64:
                import tempfile, os
                fd, tmp = tempfile.mkstemp(suffix='.png')
                os.close(fd)
                with open(tmp, 'wb') as f:
                    f.write(base64.b64decode(screenshot_b64))
                try:
                    self.telegram_bot.send_photo(self.telegram_chat_id, tmp)
                except Exception:
                    pass
                finally:
                    os.unlink(tmp)
        except Exception as e:
            self._log(f"[portfolio] Telegram ошибка: {e}")

    def _log(self, msg: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                msg, source="portfolio_filler"
            )
        else:
            print(msg)
