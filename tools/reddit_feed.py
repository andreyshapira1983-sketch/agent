"""Reddit читается через свою ленту `.rss`, а не через страницу.

24.09, чат оператора: «посмотри в Reddit» — web_fetch страницы r/LocalLLaMA
отдал заглушку «Reddit» в 6 символов: страница собирается скриптом в
браузере. JSON (`.json`) с сервера отвечает 403. Лента `.rss` отвечает 200 и
несёт посты целиком (замер 24.09: 74 КБ Atom). У каждого публичного адреса
Reddit есть лента: к пути дописывается `.rss`; работает с запуска сайта и
пережила смену правил API в 2023 (wprssaggregator.com/reddit-rss-feed,
howtogeek.com/320264). Разбор — тот же безопасный разборщик, что у rss_fetch.
"""
from __future__ import annotations

from urllib.parse import urlsplit

_HOSTS = frozenset({"reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com",
                    "np.reddit.com"})


def reddit_feed_url(url: str) -> str | None:
    """Адрес ленты для страницы Reddit; None — не Reddit или уже лента/JSON."""
    parts = urlsplit(url)
    if (parts.hostname or "").lower() not in _HOSTS:
        return None
    path = parts.path or "/"
    if path.endswith((".rss", ".json")):
        return None
    feed = f"https://www.reddit.com{path.rstrip('/')}/.rss"
    return f"{feed}?{parts.query}" if parts.query else feed


def feed_as_text(xml_text: str, *, limit: int = 25) -> str:
    """Лента -> читаемый текст: заголовок, адрес и содержание каждой записи."""
    from tools.rss_fetch import _parse_feed
    from tools.web_fetch import WebFetchTool

    title, _kind, entries = _parse_feed(xml_text, limit=limit)
    lines = [f"Reddit feed: {title}"]
    for i, entry in enumerate(entries, 1):
        body = WebFetchTool._strip_html(entry.get("summary") or "")
        lines.append(f"\n[{i}] {entry.get('title', '')}\n{entry.get('url', '')}\n"
                     f"{entry.get('published_at', '')}\n{body[:1500]}")
    return "\n".join(lines)
