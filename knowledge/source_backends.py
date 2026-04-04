# Source Backends — бесплатные источники знаний для KnowledgeAcquisitionPipeline (Слой 31)
# Архитектура автономного AI-агента
#
# Семь бэкендов — все без API-ключей:
#   ArXivBackend      — научные статьи arXiv.org
#   WikipediaBackend  — статьи Wikipedia (REST API)
#   RSSBackend        — любые RSS/Atom-ленты (новости, блоги, подкасты)
#   WeatherBackend    — погода через wttr.in (бесплатно, без ключей)
#   HackerNewsBackend — Hacker News официальный API (бесплатно)
#   PyPIBackend       — документация Python-библиотек через pypi.org/pypi/{pkg}/json
#
# Используются KnowledgeAcquisitionPipeline для автономного пополнения памяти.

import re
import time
import urllib.request
import urllib.parse
import urllib.error
import json
import socket
import ipaddress
import threading


_DNS_PIN_LOCK = threading.Lock()


def _resolve_public_ips(hostname: str, port: int | None = None) -> set[str]:
    """Резолвит hostname и возвращает только безопасные публичные IP (строки)."""
    host = str(hostname or '').strip().lower()
    if not host:
        return set()
    if host in {'localhost', '127.0.0.1', '::1'} or host.endswith('.local'):
        return set()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return set()
    safe_ips: set[str] = set()
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            continue  # Фильтруем небезопасные IP, а не отклоняем весь хост
        safe_ips.add(str(ip))
    # Если есть ТОЛЬКО небезопасные IP и ни одного публичного — отклоняем
    return safe_ips


class _PinnedDNS:
    """Временный DNS pinning: разрешает целевой host только в заранее одобренные IP.

    БЕЗОПАСНАЯ версия: не патчит глобальный socket.getaddrinfo.
    Вместо этого проверяет результат resolve и блокирует неодобренные IP.
    """

    def __init__(self, hostname: str, allowed_ips: set[str]):
        self.hostname = str(hostname or '').strip().lower()
        self.allowed_ips = set(allowed_ips or set())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        pass

    def verify(self, host: str, port: int = 80) -> None:
        """Проверяет что DNS для host резолвится только в allowed IPs."""
        host_str = str(host or '').strip().lower()
        if host_str != self.hostname:
            return
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        for info in infos:
            ip_s = str(info[4][0])
            if ip_s not in self.allowed_ips:
                raise socket.gaierror(
                    f'DNS pin mismatch for {self.hostname}: {ip_s} not in allowed set'
                )


def _html_to_text(html: str, max_chars: int | None = None) -> str:
    """Грубое преобразование HTML в читаемый plain-text."""
    text = str(html or '')
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', text)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|section|article|li|h[1-6]|tr|blockquote)>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = urllib.parse.unquote(text)
    text = re.sub(r'&nbsp;?', ' ', text)
    text = re.sub(r'&amp;', '&', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'\r', '', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = '\n'.join(line.strip() for line in text.splitlines())
    text = '\n'.join(line for line in text.splitlines() if line)
    text = text.strip()
    if max_chars is not None:
        return text[:max_chars]
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Общий помощник HTTP
# ─────────────────────────────────────────────────────────────────────────────

def _is_public_host(hostname: str) -> bool:
    """Проверяет, что hostname резолвится только в публичные IP-адреса."""
    return bool(_resolve_public_ips(hostname))


def _is_safe_http_url(url: str) -> bool:
    from safety.hardening import NetworkGuard as _NG
    parsed = urllib.parse.urlparse(str(url or '').strip())
    if parsed.scheme not in {'http', 'https'}:
        return False
    if not parsed.hostname:
        return False
    # Единая проверка через NetworkGuard (allowlist + IP-диапазоны + DNS)
    ok, _reason = _NG().is_allowed(url)
    if not ok:
        return False
    return _is_public_host(parsed.hostname)

def _http_get(url: str, timeout: int = 15, headers: dict | None = None) -> str | None:
    """Выполняет GET-запрос и возвращает текст ответа или None при ошибке."""
    if not _is_safe_http_url(url):
        return None
    parsed = urllib.parse.urlparse(str(url or '').strip())
    host = parsed.hostname or ''
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    pinned_ips = _resolve_public_ips(host, port)
    if not pinned_ips:
        return None

    req = urllib.request.Request(url)
    req.add_header('User-Agent',
                   'Mozilla/5.0 (compatible; AutonomousAgent/1.0; +research)')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        # DNS pinning: проверяем что resolve не ушёл на приватный IP (anti-rebinding)
        pin = _PinnedDNS(host, pinned_ips)
        pin.verify(host, port)
        _MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB — защита от OOM
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return None  # слишком большой ответ
            charset = 'utf-8'
            ct = resp.headers.get('Content-Type', '')
            m = re.search(r'charset=([^\s;]+)', ct)
            if m:
                charset = m.group(1)
            return raw.decode(charset, errors='replace')
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GutenbergBackend
# ─────────────────────────────────────────────────────────────────────────────

class GutenbergBackend:
    """
    Загружает книги из Project Gutenberg (gutenberg.org).

    Использование:
        gb = GutenbergBackend()
        text = gb.fetch_by_id(1342)          # Pride and Prejudice
        meta = gb.get_metadata(1342)
        results = gb.search('Tolstoy')       # поиск по автору/названию
    """

    _BASE      = 'https://www.gutenberg.org'
    _TXT_TMPL  = 'https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt'
    _META_TMPL = 'https://www.gutenberg.org/ebooks/{id}'
    _SEARCH    = 'https://www.gutenberg.org/ebooks/search/?query={q}&format=json'

    def fetch_by_id(self, book_id: int, max_chars: int = 200_000) -> str | None:
        """
        Скачивает plain-text книги по ID Gutenberg.
        Возвращает первые max_chars символов или None при ошибке.
        """
        url = self._TXT_TMPL.format(id=book_id)
        text = _http_get(url)
        if text is None:
            # fallback: попробуем формат без кэша
            url2 = f'{self._BASE}/files/{book_id}/{book_id}-0.txt'
            text = _http_get(url2)
        if text is None:
            return None
        # Убираем заголовок Gutenberg License (до *** START OF)
        start = text.find('*** START OF')
        if start != -1:
            text = text[start:]
        end = text.find('*** END OF')
        if end != -1:
            text = text[:end]
        return text[:max_chars].strip()

    def fetch_by_url(self, url: str, max_chars: int = 200_000) -> str | None:
        """Скачивает книгу по прямому URL (.txt)."""
        text = _http_get(url)
        if text is None:
            return None
        start = text.find('*** START OF')
        if start != -1:
            text = text[start:]
        end = text.find('*** END OF')
        if end != -1:
            text = text[:end]
        return text[:max_chars].strip()

    def get_metadata(self, book_id: int) -> dict:
        """
        Возвращает базовые метаданные книги (title, author, language, subjects).
        Парсит HTML страницы ebooks/{id} простыми регулярками.
        """
        url = self._META_TMPL.format(id=book_id)
        html = _http_get(url)
        if not html:
            return {'book_id': book_id, 'source': 'gutenberg'}

        meta = {'book_id': book_id, 'source': 'gutenberg',
                'url': url}

        m = re.search(r'<title>([^<]+)</title>', html)
        if m:
            title = re.sub(r'\s*\|\s*Project Gutenberg.*$', '', m.group(1).strip())
            meta['title'] = title

        m = re.search(r'itemprop="creator"[^>]*>([^<]+)<', html)
        if m:
            meta['author'] = m.group(1).strip()

        langs = re.findall(r'itemprop="inLanguage"[^>]*>([^<]+)<', html)
        if langs:
            meta['language'] = ', '.join(lang.strip() for lang in langs)

        subjects = re.findall(r'itemprop="about"[^>]*>([^<]+)<', html)
        meta['subjects'] = [s.strip() for s in subjects[:10]]

        return meta

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """
        Ищет книги по запросу. Возвращает список {'book_id', 'title', 'author'}.
        Использует страницу поиска Gutenberg.
        """
        q = urllib.parse.quote_plus(query)
        url = f'{self._BASE}/ebooks/search/?query={q}'
        html = _http_get(url)
        if not html:
            return []

        results = []
        # Парсим результаты поиска из HTML
        for m in re.finditer(
            r'href="/ebooks/(\d+)"[^>]*>\s*<span[^>]*>([^<]+)</span>',
            html
        ):
            book_id = int(m.group(1))
            title = m.group(2).strip()
            if any(r['book_id'] == book_id for r in results):
                continue
            results.append({'book_id': book_id, 'title': title,
                            'source': 'gutenberg'})
            if len(results) >= max_results:
                break

        return results


# ─────────────────────────────────────────────────────────────────────────────
# ArXivBackend
# ─────────────────────────────────────────────────────────────────────────────

class ArXivBackend:
    """
    Загружает аннотации и метаданные статей с arXiv.org через официальный API.

    Использование:
        ax = ArXivBackend()
        results = ax.search('transformer neural network', max_results=5)
        paper = ax.fetch_abstract('2303.08774')   # по arXiv ID
    """

    _API_BASE   = 'https://export.arxiv.org/api/query'
    _SEARCH_TMPL = 'https://arxiv.org/search/'
    _ABS_TMPL   = 'https://arxiv.org/abs/{id}'

    def search(self, query: str, max_results: int = 10,
               category: str | None = None) -> list[dict]:
        """
        Ищет статьи по запросу.

        Args:
            query       — поисковый запрос
            max_results — макс. число результатов
            category    — категория arXiv (cs.AI, cs.LG, physics, math и др.)

        Returns:
            Список dict с ключами: id, title, authors, summary, published, url
        """
        safe_query = ' '.join(str(query or '').split())
        if not safe_query:
            return []
        search_q = f'all:{safe_query}'
        if category:
            search_q = f'cat:{category} AND {search_q}'

        params = urllib.parse.urlencode({
            'search_query': search_q,
            'start': 0,
            'max_results': max(1, int(max_results or 1)),
            'sortBy': 'relevance',
            'sortOrder': 'descending',
        })
        url = f'{self._API_BASE}?{params}'
        xml = _http_get(url)
        if xml:
            results = self._parse_feed(xml)
            if results:
                return results
        return self._search_html(safe_query, max_results=max_results)

    def fetch_abstract(self, arxiv_id: str) -> dict | None:
        """
        Получает метаданные и аннотацию по arXiv ID (например, '2303.08774').
        """
        arxiv_id = arxiv_id.strip()
        params = urllib.parse.urlencode({
            'id_list': arxiv_id,
            'max_results': 1,
        })
        url = f'{self._API_BASE}?{params}'
        xml = _http_get(url)
        if not xml:
            return None
        results = self._parse_feed(xml)
        return results[0] if results else None

    def _search_html(self, query: str, max_results: int = 10) -> list[dict]:
        requested = max(1, int(max_results or 1))
        params = urllib.parse.urlencode({
            'query': query,
            'searchtype': 'all',
            'abstracts': 'show',
            'order': '-announced_date_first',
            'size': max(50, requested),
        })
        url = f'{self._SEARCH_TMPL}?{params}'
        html = _http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        if not html:
            return []

        results = []
        blocks = re.findall(r'(?is)<li[^>]*class="[^"]*arxiv-result[^"]*"[^>]*>(.*?)</li>', html)
        for block in blocks:
            id_match = re.search(r'(?is)<a href="https://arxiv\.org/abs/([^"]+)"', block)
            title_match = re.search(r'(?is)<p[^>]*class="title is-5 mathjax"[^>]*>(.*?)</p>', block)
            summary_match = re.search(r'(?is)<span[^>]*class="[^"]*abstract-full[^"]*"[^>]*>(.*?)</span>', block)
            authors = re.findall(r'(?is)<a href="/search/\?searchtype=author(?:&amp;|&)query=[^"]*"[^>]*>(.*?)</a>', block)
            published_match = re.search(r'(?is)(?:originally announced|submitted)\s*[^;:<]*[: ]\s*([^<\n]+)', block)

            arxiv_id = id_match.group(1).strip() if id_match else ''
            title = _html_to_text(title_match.group(1), max_chars=500) if title_match else ''
            summary = _html_to_text(summary_match.group(1), max_chars=4000) if summary_match else ''
            author_list = [_html_to_text(author, max_chars=200) for author in authors]
            published = published_match.group(1).strip() if published_match else ''
            if not arxiv_id or not title:
                continue
            results.append({
                'id': arxiv_id,
                'title': title,
                'authors': [a for a in author_list if a],
                'summary': summary,
                'published': published,
                'url': self._ABS_TMPL.format(id=arxiv_id),
                'source': 'arxiv',
            })
            if len(results) >= requested:
                break
        return results

    def fetch_text(self, arxiv_id: str, max_chars: int = 30_000) -> str | None:
        """
        Пытается получить plain-text версию статьи.
        arXiv предоставляет HTML-версию для большинства статей с 2023+.
        Для старых статей возвращает аннотацию.
        """
        # Новый HTML endpoint arXiv
        url = f'https://ar5iv.org/abs/{arxiv_id}'
        html = _http_get(url)
        if html and len(html) > 2000:
            # Убираем теги
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:max_chars]

        # Fallback — только аннотация
        paper = self.fetch_abstract(arxiv_id)
        if paper:
            return (f"Title: {paper.get('title', '')}\n"
                    f"Authors: {', '.join(paper.get('authors', []))}\n"
                    f"Published: {paper.get('published', '')}\n\n"
                    f"Abstract:\n{paper.get('summary', '')}")
        return None

    def _parse_feed(self, xml: str) -> list[dict]:
        """Парсит Atom XML ответ arXiv API."""
        entries = re.findall(r'<entry>(.*?)</entry>', xml, re.DOTALL)
        results = []
        for entry in entries:
            def _tag(tag: str, _entry=entry) -> str:
                m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', _entry, re.DOTALL)
                return m.group(1).strip() if m else ''

            arxiv_id_raw = _tag('id')
            # ID вида: http://arxiv.org/abs/2303.08774v1
            m_id = re.search(r'arxiv\.org/abs/([^\s"<]+)', arxiv_id_raw)
            arxiv_id = m_id.group(1) if m_id else arxiv_id_raw

            authors = re.findall(r'<name>([^<]+)</name>', entry)
            published = _tag('published')[:10]  # только дата

            results.append({
                'id':        arxiv_id,
                'title':     re.sub(r'\s+', ' ', _tag('title')),
                'authors':   authors,
                'summary':   re.sub(r'\s+', ' ', _tag('summary')),
                'published': published,
                'url':       f'https://arxiv.org/abs/{arxiv_id}',
                'source':    'arxiv',
            })
        return results


# ─────────────────────────────────────────────────────────────────────────────
# WikipediaBackend
# ─────────────────────────────────────────────────────────────────────────────

class WikipediaBackend:
    """
    Получает статьи из Wikipedia через официальный REST API (без ключей).

    Использование:
        wiki = WikipediaBackend(lang='ru')
        text = wiki.fetch_article('Python_(programming_language)')
        results = wiki.search('машинное обучение', limit=5)
        summary = wiki.fetch_summary('Transformer_(machine_learning_model)')
    """

    _API_TMPL   = 'https://{lang}.wikipedia.org/w/api.php'
    _REST_TMPL  = 'https://{lang}.wikipedia.org/api/rest_v1'

    def __init__(self, lang: str = 'en'):
        self.lang = lang
        self._api  = self._API_TMPL.format(lang=lang)
        self._rest = self._REST_TMPL.format(lang=lang)

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """
        Ищет статьи по запросу.
        Возвращает список {'title', 'snippet', 'pageid'}.
        """
        params = urllib.parse.urlencode({
            'action': 'query',
            'list': 'search',
            'srsearch': query,
            'srlimit': limit,
            'format': 'json',
            'utf8': 1,
        })
        url = f'{self._api}?{params}'
        data = _http_get(url)
        if not data:
            return []
        try:
            j = json.loads(data)
            results = []
            for item in j.get('query', {}).get('search', []):
                snippet = re.sub(r'<[^>]+>', '', item.get('snippet', ''))
                results.append({
                    'title':   item.get('title', ''),
                    'pageid':  item.get('pageid'),
                    'snippet': snippet,
                    'source':  'wikipedia',
                    'lang':    self.lang,
                })
            return results
        except (ValueError, TypeError, json.JSONDecodeError):
            return []

    def fetch_summary(self, title: str) -> dict | None:
        """
        Получает краткое резюме статьи (первый абзац + метаданные).
        Использует REST API /page/summary/.
        """
        t = urllib.parse.quote(title.replace(' ', '_'))
        url = f'{self._rest}/page/summary/{t}'
        data = _http_get(url)
        if not data:
            return None
        try:
            j = json.loads(data)
            return {
                'title':    j.get('title', title),
                'extract':  j.get('extract', ''),
                'url':      j.get('content_urls', {}).get('desktop', {}).get('page', ''),
                'source':   'wikipedia',
                'lang':     self.lang,
            }
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def fetch_article(self, title: str, max_chars: int = 50_000) -> str | None:
        """
        Получает полный plain-text статьи через MediaWiki API (action=parse).

        Args:
            title     — название статьи (точное, как в URL)
            max_chars — ограничение символов

        Returns:
            Текст статьи без HTML тегов или None при ошибке.
        """
        params = urllib.parse.urlencode({
            'action': 'query',
            'titles': title,
            'prop': 'extracts',
            'explaintext': True,
            'exsectionformat': 'plain',
            'format': 'json',
            'utf8': 1,
            'redirects': 1,
        })
        url = f'{self._api}?{params}'
        data = _http_get(url)
        if data:
            try:
                j = json.loads(data)
                pages = j.get('query', {}).get('pages', {})
                for page in pages.values():
                    extract = page.get('extract', '')
                    if extract:
                        return extract[:max_chars]
            except (ValueError, TypeError, json.JSONDecodeError):
                pass

        parse_params = urllib.parse.urlencode({
            'action': 'parse',
            'page': title,
            'prop': 'text',
            'format': 'json',
            'utf8': 1,
            'redirects': 1,
        })
        parse_url = f'{self._api}?{parse_params}'
        parsed_data = _http_get(parse_url)
        if not parsed_data:
            return None
        try:
            j = json.loads(parsed_data)
            html = j.get('parse', {}).get('text', {}).get('*', '')
            text = _html_to_text(html, max_chars=max_chars)
            return text or None
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def fetch_article_sections(self, title: str) -> list[dict]:
        """
        Получает статью разбитую по разделам.
        Возвращает список {'section', 'text'}.
        """
        text = self.fetch_article(title)
        if not text:
            return []

        sections = []
        current_section = 'Introduction'
        current_text = []

        for line in text.splitlines():
            if line.startswith('==') and line.endswith('=='):
                if current_text:
                    sections.append({
                        'section': current_section,
                        'text': '\n'.join(current_text).strip(),
                    })
                current_section = line.strip('= ').strip()
                current_text = []
            else:
                current_text.append(line)

        if current_text:
            sections.append({
                'section': current_section,
                'text': '\n'.join(current_text).strip(),
            })

        return sections


# ─────────────────────────────────────────────────────────────────────────────
# RSSBackend
# ─────────────────────────────────────────────────────────────────────────────

class RSSBackend:
    """
    Читает RSS / Atom ленты — без API-ключей, только HTTP.

    Поддерживает любые RSS 2.0 и Atom 1.0 ленты.
    Готовые пресеты новостных лент: get_presets().

    Использование:
        rss = RSSBackend()
        items = rss.fetch('https://feeds.bbci.co.uk/news/rss.xml', limit=10)
        feeds = rss.fetch_multiple(rss.get_presets('tech'), limit=5)
    """

    # Бесплатные RSS-ленты без ключей
    PRESETS = {
        'tech': [
            'https://feeds.feedburner.com/TechCrunch',
            'https://www.wired.com/feed/rss',
            'https://www.theverge.com/rss/index.xml',
        ],
        'science': [
            'https://www.sciencedaily.com/rss/all.xml',
            'https://feeds.nature.com/nature/rss/current',
            'https://rss.sciam.com/ScientificAmerican-Global',
        ],
        'world_news': [
            'https://feeds.bbci.co.uk/news/world/rss.xml',
            'https://rss.reuters.com/reuters/worldNews',
            'https://feeds.skynews.com/feeds/rss/world.xml',
        ],
        'ru_news': [
            'https://lenta.ru/rss',
            'https://www.rbc.ru/arc/outgoing/rss.rbc.ru/rbcnews.topnews.rss',
            'https://tass.ru/rss/v2.xml',
        ],
        'ai': [
            'https://openai.com/blog/rss.xml',
            'https://deepmind.google/blog/rss.xml',
            'https://bair.berkeley.edu/blog/feed.xml',
        ],
        'crypto': [
            'https://cointelegraph.com/rss',
            'https://coindesk.com/arc/outbound/rss/',
        ],
    }

    def fetch(self, url: str, limit: int = 20) -> list[dict]:
        """
        Читает RSS/Atom ленту.

        Returns:
            Список {'title', 'link', 'summary', 'published', 'source'}
        """
        xml = _http_get(url, timeout=15)
        if not xml:
            return []
        return self._parse(xml, url, limit)

    def fetch_multiple(self, urls: list[str],
                       limit_per_feed: int = 5) -> list[dict]:
        """Читает несколько лент, объединяет результаты."""
        all_items = []
        for url in urls:
            try:
                items = self.fetch(url, limit=limit_per_feed)
                all_items.extend(items)
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        # Сортируем по дате (новые первыми)
        all_items.sort(key=lambda x: x.get('published', ''), reverse=True)
        return all_items

    def get_presets(self, *categories: str) -> list[str]:
        """
        Возвращает URL лент для указанных категорий.

        Доступные категории: tech, science, world_news, ru_news, ai, crypto.
        Если категории не указаны — возвращает все.
        """
        selected_categories = categories or tuple(self.PRESETS.keys())
        urls = []
        for cat in selected_categories:
            urls.extend(self.PRESETS.get(cat, []))
        return urls

    def _parse(self, xml: str, source_url: str, limit: int) -> list[dict]:
        items = []

        # RSS 2.0
        raw_items = re.findall(r'<item>(.*?)</item>', xml, re.DOTALL)

        # Atom 1.0 fallback
        if not raw_items:
            raw_items = re.findall(r'<entry>(.*?)</entry>', xml, re.DOTALL)

        for entry in raw_items[:limit]:
            def _tag(t: str, _entry=entry) -> str:
                m = re.search(rf'<{t}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{t}>',
                              _entry, re.DOTALL)
                return m.group(1).strip() if m else ''

            title = re.sub(r'<[^>]+>', '', _tag('title')).strip()
            link  = _tag('link') or _tag('url') or ''
            # Для Atom: <link href="..."/>
            if not link:
                m = re.search(r'<link[^>]+href=["\']([^"\']+)["\']', entry)
                if m:
                    link = m.group(1)
            summary = re.sub(r'<[^>]+>', '',
                             _tag('description') or _tag('summary') or '').strip()
            published = (_tag('pubDate') or _tag('published') or
                         _tag('updated') or '')[:30]

            if title:
                items.append({
                    'title':     title,
                    'link':      link,
                    'summary':   summary[:500],
                    'published': published,
                    'source':    source_url,
                    'type':      'rss',
                })

        return items


# ─────────────────────────────────────────────────────────────────────────────
# WeatherBackend
# ─────────────────────────────────────────────────────────────────────────────

class WeatherBackend:
    """
    Погода через wttr.in — полностью бесплатно, без API-ключей.

    wttr.in — открытый сервис на базе weather.com + Dark Sky данных.
    Возвращает текущую погоду и прогноз на 3 дня в JSON формате.

    Использование:
        wb = WeatherBackend()
        weather = wb.fetch('Moscow')
        brief   = wb.fetch_brief('Москва')  # одна строка: "Москва: ☁️ 5°C, влажность 78%"
        text    = wb.fetch_text('London')   # красивый текстовый отчёт для памяти
    """

    _BASE = 'https://wttr.in/{city}?format=j1&lang={lang}'
    _TEXT = 'https://wttr.in/{city}?format=4'

    def __init__(self, lang: str = 'ru'):
        self.lang = lang

    def fetch(self, city: str) -> dict | None:
        """
        Полные данные о погоде (JSON).

        Returns:
            dict с ключами: city, current, forecast_3days, source
            или None при ошибке.
        """
        url = self._BASE.format(
            city=urllib.parse.quote(city), lang=self.lang
        )
        raw = _http_get(url, timeout=10)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

        cur = (data.get('current_condition') or [{}])[0]

        def _desc(lang_list, fallback=''):
            for item in lang_list:
                if item.get('lang_iso') in (self.lang, 'en'):
                    return item.get('value', fallback)
            return fallback

        current = {
            'temp_c':       cur.get('temp_C', ''),
            'feels_like_c': cur.get('FeelsLikeC', ''),
            'humidity':     cur.get('humidity', ''),
            'wind_kmph':    cur.get('windspeedKmph', ''),
            'wind_dir':     cur.get('winddir16Point', ''),
            'visibility':   cur.get('visibility', ''),
            'uv_index':     cur.get('uvIndex', ''),
            'description':  _desc(cur.get('lang', cur.get('weatherDesc', [])),
                                  cur.get('weatherDesc', [{}])[0].get('value', '')),
        }

        # Прогноз на 3 дня
        forecast = []
        for day in (data.get('weather') or []):
            hourly = day.get('hourly', [{}])
            mid    = hourly[len(hourly)//2] if hourly else {}
            forecast.append({
                'date':     day.get('date', ''),
                'max_c':    day.get('maxtempC', ''),
                'min_c':    day.get('mintempC', ''),
                'sunrise':  day.get('astronomy', [{}])[0].get('sunrise', ''),
                'sunset':   day.get('astronomy', [{}])[0].get('sunset', ''),
                'desc':     _desc(mid.get('lang', mid.get('weatherDesc', [])),
                                  mid.get('weatherDesc', [{}])[0].get('value', '')),
            })

        return {
            'city':          city,
            'current':       current,
            'forecast_3days': forecast,
            'source':        'wttr.in',
            'fetched_at':    time.strftime('%Y-%m-%d %H:%M'),
        }

    def fetch_brief(self, city: str) -> str:
        """Одна строка погоды для быстрого ответа."""
        data = self.fetch(city)
        if not data:
            return f'Погода для {city}: нет данных'
        cur = data['current']
        desc = cur.get('description', '')
        return (f"{city}: {desc}, {cur.get('temp_c')}°C "
                f"(ощущается {cur.get('feels_like_c')}°C), "
                f"влажность {cur.get('humidity')}%, "
                f"ветер {cur.get('wind_kmph')} км/ч {cur.get('wind_dir')}")

    def fetch_text(self, city: str) -> str:
        """Текстовый отчёт о погоде для сохранения в память агента."""
        data = self.fetch(city)
        if not data:
            return f'Данные о погоде для {city} недоступны.'

        cur = data['current']
        lines = [
            f"=== Погода: {city} [{data['fetched_at']}] ===",
            f"Сейчас: {cur.get('description', '')}",
            f"Температура: {cur.get('temp_c')}°C (ощущается {cur.get('feels_like_c')}°C)",
            f"Влажность: {cur.get('humidity')}%",
            f"Ветер: {cur.get('wind_kmph')} км/ч, направление {cur.get('wind_dir')}",
            f"Видимость: {cur.get('visibility')} км",
            f"УФ-индекс: {cur.get('uv_index')}",
            '',
            '--- Прогноз на 3 дня ---',
        ]
        for day in data.get('forecast_3days', []):
            lines.append(
                f"{day['date']}: {day.get('desc', '')}, "
                f"макс {day.get('max_c')}°C / мин {day.get('min_c')}°C, "
                f"восход {day.get('sunrise')}, закат {day.get('sunset')}"
            )
        return '\n'.join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# HackerNewsBackend
# ─────────────────────────────────────────────────────────────────────────────

class HackerNewsBackend:
    """
    Hacker News через официальный Firebase API — бесплатно, без ключей.

    Лучшие источники для технических новостей, стартапов и AI.

    Использование:
        hn = HackerNewsBackend()
        top    = hn.fetch_top(limit=20)
        new    = hn.fetch_new(limit=10)
        ask    = hn.fetch_ask(limit=5)
        item   = hn.fetch_item(12345)
        text   = hn.fetch_text(limit=15)   # готовый текст для памяти
    """

    _BASE = 'https://hacker-news.firebaseio.com/v0'

    def fetch_top(self, limit: int = 20) -> list[dict]:
        """Топ-истории Hacker News прямо сейчас."""
        return self._fetch_section('topstories', limit)

    def fetch_new(self, limit: int = 20) -> list[dict]:
        """Свежие истории."""
        return self._fetch_section('newstories', limit)

    def fetch_best(self, limit: int = 20) -> list[dict]:
        """Лучшие истории (по оценке редакции)."""
        return self._fetch_section('beststories', limit)

    def fetch_ask(self, limit: int = 10) -> list[dict]:
        """Ask HN вопросы."""
        return self._fetch_section('askstories', limit)

    def fetch_show(self, limit: int = 10) -> list[dict]:
        """Show HN проекты."""
        return self._fetch_section('showstories', limit)

    def fetch_item(self, item_id: int) -> dict | None:
        """Получает конкретный item по ID."""
        raw = _http_get(f'{self._BASE}/item/{item_id}.json', timeout=8)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    def fetch_text(self, limit: int = 20, section: str = 'top') -> str:
        """
        Текстовый отчёт для сохранения в память агента.

        Args:
            limit   — количество историй
            section — 'top' | 'new' | 'best' | 'ask' | 'show'
        """
        fn_map = {
            'top': self.fetch_top, 'new': self.fetch_new,
            'best': self.fetch_best, 'ask': self.fetch_ask,
            'show': self.fetch_show,
        }
        items = fn_map.get(section, self.fetch_top)(limit=limit)
        if not items:
            return 'Hacker News: нет данных'

        lines = [f"=== Hacker News ({section}) [{time.strftime('%Y-%m-%d %H:%M')}] ==="]
        for i, item in enumerate(items, 1):
            score = item.get('score', 0)
            comments = item.get('descendants', 0)
            lines.append(
                f"{i}. [{score}↑ {comments}💬] {item.get('title', '')}\n"
                f"   {item.get('url', 'news.ycombinator.com/item?id=' + str(item.get('id', '')))}"
            )
        return '\n'.join(lines)

    def _fetch_section(self, section: str, limit: int) -> list[dict]:
        raw = _http_get(f'{self._BASE}/{section}.json', timeout=10)
        if not raw:
            return []
        try:
            ids = json.loads(raw)[:limit * 2]  # берём с запасом
        except (ValueError, TypeError, json.JSONDecodeError):
            return []

        items = []
        for item_id in ids:
            if len(items) >= limit:
                break
            item = self.fetch_item(item_id)
            if item and item.get('type') in ('story', 'ask', 'show'):
                items.append({
                    'id':          item.get('id'),
                    'title':       item.get('title', ''),
                    'url':         item.get('url', ''),
                    'score':       item.get('score', 0),
                    'descendants': item.get('descendants', 0),
                    'by':          item.get('by', ''),
                    'time':        item.get('time', 0),
                    'type':        item.get('type', ''),
                    'source':      'hackernews',
                })
            time.sleep(0.05)   # вежливый rate-limit
        return items


# ─────────────────────────────────────────────────────────────────────────────
# PyPIBackend
# ─────────────────────────────────────────────────────────────────────────────

class PyPIBackend:
    """
    Получает метаданные и документацию Python-пакетов через PyPI JSON API.
    Не требует API-ключей: https://pypi.org/pypi/{package}/json

    Использование:
        pypi = PyPIBackend()
        meta    = pypi.fetch_package('requests')      # полные метаданные
        version = pypi.latest_version('numpy')        # '2.1.0'
        docs    = pypi.fetch_docs('aiohttp')          # текст описания
        results = pypi.search_names('http client', n=5)  # поиск по PyPI
    """

    _BASE = 'https://pypi.org/pypi/{name}/json'
    _SEARCH = 'https://pypi.org/search/?q={q}'

    def fetch_package(self, name: str) -> dict | None:
        """
        Возвращает метаданные пакета с PyPI.

        Returns:
            dict с ключами: name, version, summary, description, author,
            license, home_page, docs_url, project_urls, requires_python,
            keywords, classifiers, source
            или None если пакет не найден.
        """
        name = name.strip().lower()
        url = self._BASE.format(name=name)
        raw = _http_get(url, timeout=10)
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

        info = data.get('info', {})
        return {
            'name':            info.get('name', name),
            'version':         info.get('version', ''),
            'summary':         info.get('summary', ''),
            'description':     (info.get('description', '')[:8000]
                                if info.get('description') else ''),
            'author':          info.get('author', ''),
            'license':         info.get('license', ''),
            'home_page':       info.get('home_page', ''),
            'docs_url':        (info.get('docs_url') or
                                info.get('project_urls', {}).get('Documentation', '') or
                                info.get('project_urls', {}).get('Changelog', '')),
            'project_urls':    info.get('project_urls', {}),
            'requires_python': info.get('requires_python', ''),
            'keywords':        info.get('keywords', ''),
            'classifiers':     info.get('classifiers', [])[:20],
            'source':          'pypi',
        }

    def latest_version(self, name: str) -> str | None:
        """Возвращает последнюю стабильную версию пакета или None."""
        pkg = self.fetch_package(name)
        return pkg['version'] if pkg else None

    def fetch_docs(self, name: str, max_chars: int = 10_000) -> str | None:
        """
        Возвращает текстовое описание пакета из PyPI (поле description).
        Это README или документация, опубликованная автором.

        Args:
            name      — имя пакета (например 'requests', 'numpy')
            max_chars — ограничение символов

        Returns:
            Текст описания пакета или None при ошибке.
        """
        pkg = self.fetch_package(name)
        if not pkg:
            return None
        desc = pkg.get('description', '').strip()
        if not desc:
            # Fallback: только summary
            return pkg.get('summary', '')
        # Убираем RST/Markdown разметку (базовая очистка)
        desc = re.sub(r'```[\s\S]*?```', '', desc)  # code blocks
        desc = re.sub(r'`[^`]+`', lambda m: m.group(0)[1:-1], desc)  # inline code
        desc = re.sub(r'^#+\s+', '', desc, flags=re.MULTILINE)  # headers
        desc = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', desc)  # links
        desc = re.sub(r'[ \t]{2,}', ' ', desc)
        desc = re.sub(r'\n{3,}', '\n\n', desc)
        return desc[:max_chars].strip()

    def fetch_text(self, name: str) -> str:
        """
        Готовый текстовый отчёт о пакете для сохранения в память агента.

        Включает: версию, описание, автора, лицензию, ссылки, классификаторы.
        """
        pkg = self.fetch_package(name)
        if not pkg:
            return f'PyPI: пакет "{name}" не найден.'

        lines = [
            f"=== PyPI: {pkg['name']} v{pkg['version']} ===",
            f"Описание: {pkg.get('summary', '')}",
        ]
        if pkg.get('author'):
            lines.append(f"Автор: {pkg['author']}")
        if pkg.get('license'):
            lines.append(f"Лицензия: {pkg['license']}")
        if pkg.get('requires_python'):
            lines.append(f"Python: {pkg['requires_python']}")
        if pkg.get('keywords'):
            lines.append(f"Ключевые слова: {pkg['keywords']}")
        if pkg.get('home_page'):
            lines.append(f"Сайт: {pkg['home_page']}")
        if pkg.get('docs_url'):
            lines.append(f"Документация: {pkg['docs_url']}")
        if pkg.get('project_urls'):
            for label, url in list(pkg['project_urls'].items())[:4]:
                lines.append(f"  {label}: {url}")

        desc = pkg.get('description', '').strip()
        if desc:
            # Показываем первые 3000 символов описания
            lines.append('')
            lines.append('--- README / Описание (фрагмент) ---')
            lines.append(desc[:3000])

        return '\n'.join(lines)

    def search_names(self, query: str, n: int = 10) -> list[dict]:
        """
        Ищет пакеты по имени/описанию через страницу поиска PyPI.

        Args:
            query — строка поиска (например 'http client', 'machine learning')
            n     — максимальное количество результатов

        Returns:
            Список dict с ключами: name, version, summary, source.
        """
        q = urllib.parse.quote_plus(query)
        url = self._SEARCH.format(q=q)
        html = _http_get(url, timeout=12)
        if not html:
            return []

        results = []
        # PyPI search страница: <span class="package-snippet__name"> / <span class="package-snippet__version">
        names = re.findall(
            r'<span[^>]+class="package-snippet__name"[^>]*>([^<]+)</span>',
            html
        )
        versions = re.findall(
            r'<span[^>]+class="package-snippet__version"[^>]*>([^<]+)</span>',
            html
        )
        summaries = re.findall(
            r'<p[^>]+class="package-snippet__description"[^>]*>([^<]+)</p>',
            html
        )

        for i, name in enumerate(names[:n]):
            results.append({
                'name':    name.strip(),
                'version': versions[i].strip() if i < len(versions) else '',
                'summary': summaries[i].strip() if i < len(summaries) else '',
                'source':  'pypi',
            })

        return results
