# Web Crawler — реальный HTTP-краулер для Perception Layer (Слой 1)
# Подключается к PerceptionLayer(web_crawler=WebCrawler())


import re
import time
import socket
import ipaddress
import importlib
from urllib.parse import urljoin, urlparse


class WebCrawler:
    """
    HTTP-краулер на основе requests + BeautifulSoup.

    Устанавливается: pip install requests beautifulsoup4 lxml

    Реализует интерфейс:
        crawler.fetch(url)               → dict с text/title/links
        crawler.crawl(start_url, depth)  → list[dict]
    """

    def __init__(self, timeout: int = 15, max_content_kb: int = 200,
                 delay: float = 0.5):
        """
        Args:
            timeout        — таймаут HTTP запроса (секунды)
            max_content_kb — максимальный размер страницы для парсинга
            delay          — задержка между запросами (секунды)
        """
        self.timeout = timeout
        self.max_content_kb = max_content_kb
        self.delay = delay
        self._last_request = 0.0

        try:
            requests = importlib.import_module('requests')
            self._requests = requests
        except ImportError as exc:
            raise ImportError("Установи requests: pip install requests") from exc

        self._session = self._requests.Session()
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (compatible; AutonomousAgent/1.0; "
                "+https://github.com/agent)"
            )
        })

    # ── Основной интерфейс ────────────────────────────────────────────────────

    def fetch(self, url: str) -> dict:
        """
        Загружает и парсит веб-страницу.

        Returns:
            {
                'url': str,
                'title': str,
                'text': str,       # очищенный текст без HTML тегов
                'links': list[str],
                'status': int,
                'success': bool,
            }
        """
        if not self._is_safe_url(url):
            return {
                'url': url, 'title': '', 'text': '',
                'links': [], 'status': 0,
                'success': False,
                'error': 'URL отклонён политикой безопасности (SSRF protection)',
            }

        self._throttle()
        try:
            resp = self._session.get(url, timeout=self.timeout, allow_redirects=True)
            self._last_request = time.time()

            if len(resp.content) > self.max_content_kb * 1024:
                content = resp.content[:self.max_content_kb * 1024]
            else:
                content = resp.content

            result = self._parse_html(url, content, resp.headers.get('content-type', ''))
            result['status'] = resp.status_code
            result['success'] = resp.status_code < 400
            return result

        except (self._requests.RequestException, ValueError, OSError) as e:
            return {
                'url': url, 'title': '', 'text': '',
                'links': [], 'status': 0,
                'success': False, 'error': str(e),
            }

    def crawl(self, start_url: str, depth: int = 1,
              max_pages: int = 10) -> list[dict]:
        """
        Обходит сайт начиная с start_url на глубину depth.

        Returns:
            Список страниц, каждая в формате fetch().
        """
        visited = set()
        queue = [(start_url, 0)]
        results = []
        base_domain = urlparse(start_url).netloc

        while queue and len(results) < max_pages:
            url, current_depth = queue.pop(0)
            if url in visited:
                continue
            if not self._is_safe_url(url):
                continue
            visited.add(url)

            page = self.fetch(url)
            results.append(page)

            if current_depth < depth:
                for link in page.get('links', [])[:5]:
                    link_domain = urlparse(link).netloc
                    if link_domain == base_domain and link not in visited and self._is_safe_url(link):
                        queue.append((link, current_depth + 1))

        return results

    def fetch_text(self, url: str) -> str:
        """Возвращает только очищенный текст страницы."""
        return self.fetch(url).get('text', '')

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_html(self, url: str, content: bytes, content_type: str) -> dict:
        try:
            BeautifulSoup = getattr(importlib.import_module('bs4'), 'BeautifulSoup')

            encoding = 'utf-8'
            if 'charset=' in content_type:
                encoding = content_type.split('charset=')[-1].strip()

            soup = BeautifulSoup(content, 'lxml',
                                 from_encoding=encoding)

            # Удаляем ненужные теги
            for tag in soup(['script', 'style', 'nav', 'footer',
                              'iframe', 'noscript', 'head']):
                tag.decompose()

            title = soup.title.string.strip() if soup.title and soup.title.string else ''
            text = ' '.join(soup.get_text(separator=' ').split())[:5000]

            links = []
            for a in soup.find_all('a', href=True)[:30]:
                href = urljoin(url, str(a['href']))
                if href.startswith('http'):
                    links.append(href)

            return {'url': url, 'title': title, 'text': text, 'links': links}

        except ImportError:
            # Fallback: strip HTML with regex
            text = re.sub(r'<[^>]+>', ' ', content.decode('utf-8', errors='replace'))
            text = ' '.join(text.split())[:5000]
            return {'url': url, 'title': '', 'text': text, 'links': []}

    def _throttle(self):
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def _is_safe_url(self, url: str) -> bool:
        """Блокирует SSRF-цели: не-http(s), localhost, приватные и link-local адреса."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return False

            host = (parsed.hostname or '').strip().lower()
            if not host:
                return False

            if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.local'):
                return False

            # Прямой IP в URL
            try:
                ip = ipaddress.ip_address(host)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                ):
                    return False
                return True
            except ValueError:
                pass

            # DNS-имя: проверяем все резолвящиеся адреса (таймаут 5 сек против зависания)
            _old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(5)
            try:
                infos = socket.getaddrinfo(host, parsed.port or 80, proto=socket.IPPROTO_TCP)
            finally:
                socket.setdefaulttimeout(_old_timeout)
            for info in infos:
                ip_text = info[4][0]
                ip = ipaddress.ip_address(ip_text)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                ):
                    return False
            return True
        except (ValueError, OSError, socket.gaierror):
            return False
