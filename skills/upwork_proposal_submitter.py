# Upwork Proposal Submitter — автоматическая подача заявок через браузер (Playwright)
# Паттерн: навигация → клик «Submit Proposal» → заполнение формы → подтверждение → скриншот

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProposalResult:
    success: bool
    message: str
    screenshot_b64: Optional[str] = None


# ── Селекторы (несколько вариантов на случай обновления DOM Upwork) ────────

_BTN_SUBMIT_PROPOSAL = [
    'button[data-test="submit-proposal-btn"]',
    'a[data-test="submit-proposal-btn"]',
    'button:has-text("Submit a Proposal")',
    'a:has-text("Submit a Proposal")',
    'button:has-text("Apply Now")',
    'a:has-text("Apply Now")',
]

_COVER_LETTER_FIELD = [
    'textarea[data-test="cover-letter"]',
    'textarea[aria-label*="cover letter" i]',
    'textarea[placeholder*="cover letter" i]',
    'textarea[name*="coverLetter"]',
    '#cover_letter',
    'textarea.cover-letter',
    'div[data-test="proposal-cover-letter"] textarea',
    'textarea',  # последний fallback — первый textarea на странице
]

_BID_AMOUNT_FIELD = [
    'input[data-test="bid-amount"]',
    'input[aria-label*="bid" i]',
    'input[aria-label*="rate" i]',
    'input[data-test="hourly-rate"]',
    'input[placeholder*="amount" i]',
    'input[placeholder*="rate" i]',
    'input[name*="amount" i]',
    'input[name*="rate" i]',
]

_BTN_FINAL_SUBMIT = [
    'button[data-test="submit-proposal"]',
    'button:has-text("Submit")',
    'button:has-text("Send")',
    'button[type="submit"]',
]

_LOGIN_INDICATOR = [
    'input[name="login"]',
    'input[id="login_username"]',
    '#login_username',
]


class UpworkProposalSubmitter:
    """
    Подаёт заявку (proposal) на вакансию Upwork через браузер.

    Интеграция:
      submitter = UpworkProposalSubmitter(browser_tool, telegram_bot, chat_id)
      result = submitter.submit(job_url, cover_letter, bid_amount)

    Перед подачей запрашивает одобрение через Telegram (request_approval).
    """

    def __init__(
        self,
        browser_tool: Any = None,
        telegram_bot: Any = None,
        telegram_chat_id: str | int | None = None,
        monitoring: Any = None,
        default_hourly_rate: float = 25.0,
        approval_timeout: int = 600,
    ):
        self.browser_tool = browser_tool
        self.telegram_bot = telegram_bot
        self.telegram_chat_id = telegram_chat_id
        self.monitoring = monitoring
        self.default_hourly_rate = default_hourly_rate
        self.approval_timeout = approval_timeout  # секунд ждать одобрения

    # ── Публичный метод ───────────────────────────────────────────────────────

    def submit(
        self,
        job_url: str,
        cover_letter: str,
        bid_amount: float | None = None,
        job_title: str = "",
        skip_approval: bool = False,
    ) -> ProposalResult:
        """
        Полный цикл подачи заявки.

        1. Запрашивает одобрение в Telegram (если skip_approval=False)
        2. Открывает браузер → навигация к вакансии
        3. Нажимает «Submit a Proposal»
        4. Заполняет cover letter + bid
        5. Нажимает финальный Submit
        6. Делает скриншот результата
        """
        bid = bid_amount or self.default_hourly_rate

        # ── Шаг 0: запрос одобрения ──────────────────────────────────────────
        if not skip_approval:
            approved = self._request_human_approval(job_url, cover_letter, bid, job_title)
            if not approved:
                self._log("Подача отклонена пользователем или таймаут.")
                return ProposalResult(success=False, message="Отклонено пользователем")

        # ── Шаг 1–5: браузерная автоматизация ────────────────────────────────
        own_browser = False
        browser = self.browser_tool
        try:
            if browser is None:
                from tools.browser_tool import BrowserTool
                browser = BrowserTool(headless=False, timeout=60000)
                own_browser = True

            return self._run_submission(browser, job_url, cover_letter, bid, job_title)
        except Exception as e:
            msg = f"Ошибка подачи заявки: {e}"
            self._log(msg)
            return ProposalResult(success=False, message=msg)
        finally:
            if own_browser and browser:
                try:
                    browser.close()
                except Exception:
                    pass

    # ── Запрос одобрения ──────────────────────────────────────────────────────

    def _request_human_approval(
        self, job_url: str, cover_letter: str, bid: float, job_title: str
    ) -> bool:
        if not self.telegram_bot or not self.telegram_chat_id:
            self._log("Telegram не настроен — автоматическое одобрение.")
            return True

        if not hasattr(self.telegram_bot, 'request_approval'):
            self._log("request_approval отсутствует — автоматическое одобрение.")
            return True

        # Предварительное уведомление с деталями
        preview = (
            f"📋 <b>Запрос на подачу заявки</b>\n\n"
            f"<b>Вакансия:</b> {job_title[:200] if job_title else '—'}\n"
            f"🔗 {job_url}\n\n"
            f"💰 <b>Ставка:</b> ${bid}/час\n\n"
            f"📝 <b>Письмо:</b>\n<i>{cover_letter[:1500]}</i>"
        )
        try:
            self.telegram_bot.send(self.telegram_chat_id, preview)
        except Exception as e:
            self._log(f"Не удалось отправить превью: {e}")

        try:
            return self.telegram_bot.request_approval(
                action_type="upwork_proposal",
                payload={
                    "job_url": job_url,
                    "bid": bid,
                    "title": job_title[:100],
                },
                timeout=self.approval_timeout,
            )
        except Exception as e:
            self._log(f"Ошибка request_approval: {e}")
            return False

    # ── Браузерная автоматизация ──────────────────────────────────────────────

    def _run_submission(
        self, browser: Any, job_url: str, cover_letter: str, bid: float, job_title: str
    ) -> ProposalResult:
        # Навигация к вакансии
        self._log(f"Открываю вакансию: {job_url}")
        browser.navigate(job_url)
        time.sleep(3)

        # Проверка: нужен ли логин?
        if self._needs_login(browser):
            self._notify("⏳ Нужен логин на Upwork. Залогинься в открытом браузере (2 мин).")
            if not self._wait_for_login(browser, timeout=120):
                return ProposalResult(
                    success=False,
                    message="Таймаут ожидания логина",
                    screenshot_b64=browser.screenshot(),
                )
            # После логина повторно переходим к вакансии
            browser.navigate(job_url)
            time.sleep(3)

        # Скриншот страницы вакансии
        self._screenshot_step(browser, "Страница вакансии загружена")

        # Нажимаем «Submit a Proposal»
        if not self._click_first(browser, _BTN_SUBMIT_PROPOSAL):
            # Попробуем через прямой URL /proposals/job/~XXXXX/apply
            apply_url = self._build_apply_url(job_url)
            if apply_url:
                self._log(f"Кнопка не найдена, пробую прямой URL: {apply_url}")
                browser.navigate(apply_url)
                time.sleep(3)
            else:
                return ProposalResult(
                    success=False,
                    message="Не найдена кнопка Submit Proposal",
                    screenshot_b64=browser.screenshot(),
                )
        else:
            time.sleep(3)

        self._screenshot_step(browser, "Форма подачи заявки")

        # Заполняем cover letter
        if not self._fill_first(browser, _COVER_LETTER_FIELD, cover_letter):
            return ProposalResult(
                success=False,
                message="Не найдено поле cover letter",
                screenshot_b64=browser.screenshot(),
            )

        # Заполняем bid amount (если поле есть)
        bid_str = str(bid)
        self._fill_first(browser, _BID_AMOUNT_FIELD, bid_str)
        # Если поле bid не найдено — не критично (может быть fixed-price без ввода)

        self._screenshot_step(browser, "Форма заполнена")

        # Прокручиваем вниз чтобы кнопка Submit стала видна
        browser.scroll(0, 500)
        time.sleep(1)

        # Финальный Submit
        if not self._click_first(browser, _BTN_FINAL_SUBMIT):
            return ProposalResult(
                success=False,
                message="Не найдена кнопка финальной отправки",
                screenshot_b64=browser.screenshot(),
            )

        time.sleep(5)  # ждём ответа сервера
        shot = browser.screenshot()

        # Проверяем результат по URL или присутствию текста
        current = getattr(browser, 'current_url', '') or ''
        page_text = ''
        try:
            page_text = browser.get_text() or ''
        except Exception:
            pass

        success_indicators = [
            'proposal submitted' in page_text.lower(),
            'successfully' in page_text.lower(),
            '/proposals' in current.lower(),
            'thank' in page_text.lower(),
        ]

        if any(success_indicators):
            msg = f"✅ Заявка подана: {job_title[:80]}"
            self._notify(msg)
            self._log(msg)
            return ProposalResult(success=True, message="Заявка подана", screenshot_b64=shot)
        else:
            msg = f"⚠️ Не уверен в результате подачи: {job_title[:80]}"
            self._notify(msg)
            self._log(msg)
            return ProposalResult(
                success=False,
                message="Не удалось подтвердить успешность подачи",
                screenshot_b64=shot,
            )

    # ── Вспомогательные ───────────────────────────────────────────────────────

    def _needs_login(self, browser: Any) -> bool:
        for sel in _LOGIN_INDICATOR:
            try:
                if browser.wait_for(sel, timeout=2000):
                    return True
            except Exception:
                pass
        return False

    def _wait_for_login(self, browser: Any, timeout: int = 120) -> bool:
        """Ждём, пока пользователь залогинится (пока исчезнет login-форма)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            still_login = False
            for sel in _LOGIN_INDICATOR:
                try:
                    if browser.wait_for(sel, timeout=1000):
                        still_login = True
                        break
                except Exception:
                    pass
            if not still_login:
                self._log("Логин обнаружен!")
                return True
            time.sleep(3)
        return False

    def _click_first(self, browser: Any, selectors: list[str]) -> bool:
        for sel in selectors:
            try:
                if browser.click(sel):
                    self._log(f"Клик: {sel}")
                    return True
            except Exception:
                pass
        return False

    def _fill_first(self, browser: Any, selectors: list[str], text: str) -> bool:
        for sel in selectors:
            try:
                if browser.fill(sel, text):
                    self._log(f"Заполнено: {sel}")
                    return True
            except Exception:
                pass
        return False

    def _build_apply_url(self, job_url: str) -> str | None:
        """Пытается построить прямой URL формы подачи из URL вакансии."""
        # https://www.upwork.com/jobs/~01abc → https://www.upwork.com/proposals/job/~01abc/apply
        import re as _re
        m = _re.search(r'/jobs/(~\w+)', job_url)
        if m:
            job_id = m.group(1)
            return f"https://www.upwork.com/proposals/job/{job_id}/apply"
        return None

    def _screenshot_step(self, browser: Any, step_name: str):
        try:
            shot = browser.screenshot()
            self._notify(f"📸 {step_name}")
            self._log(f"Скриншот: {step_name}")
        except Exception as e:
            self._log(f"Скриншот не удался ({step_name}): {e}")

    def _notify(self, text: str):
        if self.telegram_bot and self.telegram_chat_id:
            try:
                self.telegram_bot.send(self.telegram_chat_id, text)
            except Exception as e:
                self._log(f"Telegram notify ошибка: {e}")

    def _log(self, msg: str):
        if self.monitoring:
            self.monitoring.info(msg, source="upwork_proposal_submitter")
        else:
            logger.info(msg)
