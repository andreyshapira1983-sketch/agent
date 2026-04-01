# llm — Реальные клиенты и backends для подключения агента к внешним сервисам
# OpenAI LLM, Telegram алерты, GitHub API, поиск DuckDuckGo, HTTP-краулер
from .openai_client import OpenAIClient
from .local_backend import LocalNeuralBackend
from .telegram_sink import TelegramSink
from .github_backend import GitHubBackend
from .search_backend import DuckDuckGoBackend
from .web_crawler import WebCrawler

__all__ = [
    'OpenAIClient',
    'LocalNeuralBackend',
    'TelegramSink',
    'GitHubBackend',
    'DuckDuckGoBackend',
    'WebCrawler',
]
