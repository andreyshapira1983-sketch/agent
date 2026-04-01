# Browser Tool — инструмент Tool Layer (Слой 5)
# Архитектура автономного AI-агента
# Управление браузером через Playwright: навигация, клик, заполнение форм, скриншоты.
# pylint: disable=broad-except,unused-import

from __future__ import annotations

import os
import time
import base64
from typing import Any


class BrowserPage:
    """Результат загрузки страницы."""

    def __init__(self, url: str, title: str, text: str, html: str,
                 screenshot_b64: str | None = None, links: list[str] | None = None,
                 status: int = 200, success: bool = True, error: str | None = None):
        self.url = url
        self.title = title
        self.text = text
        self.html = html
        self.screenshot_b64 = screenshot_b64
        self.links = links or []
        self.status = status
        self.success = success
        self.error = error

    def to_dict(self) -> dict:
        return {
            'url': self.url,
            'title': self.title,
            'text_preview': self.text[:500],
            'links_count': len(self.links),
            'status': self.status,
            'success': self.success,
            'error': self.error,
        }


class BrowserTool:
    """
    Web Browser Tool — Слой 5.

    Управляет headless-браузером через Playwright для полноценного
    взаимодействия с веб-страницами (JavaScript, динамический контент).

    Установка: pip install playwright && playwright install chromium

    Методы:
        navigate(url)               — открыть страницу
        click(selector)             — кликнуть элемент
        fill(selector, text)        — заполнить поле
        submit(selector)            — нажать кнопку
        screenshot(path)            — сделать скриншот
        extract_text()              — извлечь текст страницы
        get_links()                 — список ссылок
        wait_for(selector, timeout) — дождаться элемента
        close()                     — закрыть браузер

    Fallback: если Playwright не установлен — делегирует в WebCrawler (requests).
    """

    def __init__(self, headless: bool = True, timeout: int = 30000,
                 monitoring=None, web_crawler=None):
        """
        Args:
            headless    — запуск без GUI (True для серверов)
            timeout     — таймаут операций в мс
            web_crawler — fallback если Playwright недоступен
        """
        self.name = 'browser'
        self.description = 'Headless-браузер (Playwright) для взаимодействия с веб-страницами'
        self.headless = headless
        self.timeout = timeout
        self.monitoring = monitoring
        self.web_crawler = web_crawler

        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._playwright: Any = None
        self._backend = 'none'
        self._current_url = ''

    # ── Инициализация ─────────────────────────────────────────────────────────

    def _ensure_browser(self) -> bool:
        """Запускает браузер если ещё не запущен."""
        if self._page is not None:
            return True

        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                ),
                viewport={'width': 1280, 'height': 720},
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout)
            self._backend = 'playwright'
            self._log('Playwright браузер запущен')
            return True
        except ImportError:
            self._backend = 'crawler'
            self._log(
                'Playwright не установлен. '
                'Установи: pip install playwright && playwright install chromium',
                level='warning',
            )
            return False
        except Exception as e:
            self._backend = 'crawler'
            self._log(f'Ошибка запуска браузера: {e}', level='error')
            return False

    # ── Навигация ─────────────────────────────────────────────────────────────

    def navigate(self, url: str, wait_until: str = 'domcontentloaded') -> BrowserPage:
        """
        Открывает URL и возвращает содержимое страницы.

        Args:
            url        — адрес страницы
            wait_until — 'load' / 'domcontentloaded' / 'networkidle'
        """
        if not self._ensure_browser() or self._backend == 'crawler':
            return self._fallback_fetch(url)

        try:
            response = self._page.goto(url, wait_until=wait_until,
                                        timeout=self.timeout)
            self._current_url = self._page.url
            status = response.status if response else 200

            title = self._page.title()
            text = self._extract_text_pw()
            html = self._page.content()
            links = self._extract_links_pw()

            self._log(f'navigate: {url} -> {status}')
            return BrowserPage(
                url=self._current_url,
                title=title,
                text=text,
                html=html,
                links=links,
                status=status,
                success=200 <= status < 400,
            )
        except Exception as e:
            self._log(f'navigate error: {e}', level='error')
            return BrowserPage(url=url, title='', text='', html='',
                               success=False, error=str(e))

    # ── Взаимодействие ────────────────────────────────────────────────────────

    def click(self, selector: str) -> bool:
        """Кликает элемент по CSS/XPath селектору."""
        if not self._page:
            return False
        try:
            self._page.click(selector, timeout=self.timeout)
            return True
        except Exception as e:
            self._log(f'click({selector}): {e}', level='error')
            return False

    def fill(self, selector: str, text: str) -> bool:
        """Заполняет текстовое поле."""
        if not self._page:
            return False
        try:
            self._page.fill(selector, text)
            return True
        except Exception as e:
            self._log(f'fill({selector}): {e}', level='error')
            return False

    def press(self, selector: str, key: str) -> bool:
        """Нажимает клавишу в элементе (например Enter)."""
        if not self._page:
            return False
        try:
            self._page.press(selector, key)
            return True
        except Exception as e:
            self._log(f'press({selector}, {key}): {e}', level='error')
            return False

    def wait_for(self, selector: str, timeout: int | None = None) -> bool:
        """Ожидает появления элемента."""
        if not self._page:
            return False
        try:
            self._page.wait_for_selector(
                selector, timeout=timeout or self.timeout
            )
            return True
        except Exception:
            return False

    def evaluate(self, js: str):
        """Выполняет JavaScript на странице."""
        if not self._page:
            return None
        try:
            return self._page.evaluate(js)
        except Exception as e:
            self._log(f'evaluate: {e}', level='error')
            return None

    def scroll(self, x: int = 0, y: int = 500):
        """Прокручивает страницу."""
        if self._page:
            self._page.evaluate(f'window.scrollBy({x}, {y})')

    # ── Извлечение контента ───────────────────────────────────────────────────

    def get_text(self) -> str:
        """Возвращает текстовое содержимое текущей страницы."""
        if not self._page:
            return ''
        return self._extract_text_pw()

    def get_links(self) -> list[str]:
        """Возвращает все ссылки текущей страницы."""
        if not self._page:
            return []
        return self._extract_links_pw()

    def get_element_text(self, selector: str) -> str:
        """Возвращает текст элемента."""
        if not self._page:
            return ''
        try:
            return self._page.locator(selector).first.inner_text()
        except Exception:
            return ''

    def get_attribute(self, selector: str, attribute: str) -> str:
        """Возвращает атрибут элемента."""
        if not self._page:
            return ''
        try:
            return self._page.get_attribute(selector, attribute) or ''
        except Exception:
            return ''

    # ── Скриншоты ─────────────────────────────────────────────────────────────

    def screenshot(self, path: str | None = None, full_page: bool = True) -> str | None:
        """
        Делает скриншот. Если path указан — сохраняет файл.
        Возвращает base64-строку PNG.
        """
        if not self._page:
            return None
        try:
            kwargs: dict = {'full_page': full_page}
            if path:
                os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
                kwargs['path'] = path
            data = self._page.screenshot(**kwargs)
            return base64.b64encode(data).decode('utf-8')
        except Exception as e:
            self._log(f'screenshot: {e}', level='error')
            return None

    # ── Управление браузером ──────────────────────────────────────────────────

    def new_tab(self) -> bool:
        """Открывает новую вкладку."""
        if not self._context:
            return False
        try:
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout)
            return True
        except Exception:
            return False

    def close(self):
        """Закрывает браузер."""
        try:
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        finally:
            self._browser = None
            self._context = None
            self._page = None
            self._playwright = None
            self._log('Браузер закрыт')

    @property
    def current_url(self) -> str:
        if self._page:
            try:
                return self._page.url
            except Exception:
                pass
        return self._current_url

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_text_pw(self) -> str:
        """Извлекает чистый текст через Playwright."""
        try:
            return self._page.evaluate(
                '''() => {
                    const els = document.querySelectorAll("script,style,nav,footer,aside");
                    els.forEach(e => e.remove());
                    return document.body ? document.body.innerText : document.documentElement.innerText;
                }'''
            ) or ''
        except Exception:
            return self._page.inner_text('body') or ''

    def _extract_links_pw(self) -> list[str]:
        """Извлекает все ссылки."""
        try:
            hrefs = self._page.evaluate(
                '() => Array.from(document.querySelectorAll("a[href]"))'
                '.map(a => a.href).filter(h => h.startsWith("http"))'
            )
            return hrefs[:100]
        except Exception:
            return []

    def _fallback_fetch(self, url: str) -> BrowserPage:
        """Fallback через WebCrawler если Playwright недоступен."""
        if self.web_crawler:
            try:
                result = self.web_crawler.fetch(url)
                return BrowserPage(
                    url=result.get('url', url),
                    title=result.get('title', ''),
                    text=result.get('text', ''),
                    html='',
                    links=result.get('links', []),
                    status=result.get('status', 200),
                    success=result.get('success', True),
                )
            except Exception as e:
                return BrowserPage(url=url, title='', text='', html='',
                                   success=False, error=str(e))
        return BrowserPage(
            url=url, title='', text='', html='',
            success=False,
            error='Playwright не установлен. pip install playwright && playwright install chromium',
        )

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            getattr(self.monitoring, level, self.monitoring.info)(
                message, source='browser_tool'
            )
        else:
            print(f'[BrowserTool] {message}')

    def __del__(self):
        """Автоматически закрываем браузер при удалении объекта."""
        try:
            self.close()
        except Exception:
            pass
