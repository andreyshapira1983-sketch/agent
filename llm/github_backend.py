# GitHub Backend — реальный клиент GitHub REST API v3
# Подключается к GitHubTool(backend=GitHubBackend(token=...))

import importlib


class GitHubBackend:
    """
    Клиент GitHub API v3 на основе requests.

    Реализует методы, которые вызывает GitHubTool:
        get_repo(owner, repo)
        list_issues(owner, repo, state)
        create_issue(owner, repo, title, body)
        get_file(owner, repo, path)
        create_pr(owner, repo, title, head, base, body)
        list_repos(username)
        search_code(query)
    """

    BASE = "https://api.github.com"

    def __init__(self, token: str):
        try:
            requests = importlib.import_module('requests')
            self._requests = requests
        except ImportError as exc:
            raise ImportError("Установи requests: pip install requests") from exc

        self._token = token
        self._base_headers = {
            "Accept": "application/vnd.github.v3+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def __repr__(self) -> str:
        masked = "***" if self._token else "<empty>"
        return f"GitHubBackend(token={masked})"

    def _headers(self) -> dict:
        return {
            **self._base_headers,
            "Authorization": f"token {self._token}",
        }

    # ── Репозитории ───────────────────────────────────────────────────────────

    def get_repo(self, owner: str, repo: str) -> dict:
        """Информация о репозитории."""
        return self._get(f"/repos/{owner}/{repo}")

    def list_repos(self, username: str, per_page: int = 30) -> list:
        """Список репозиториев пользователя."""
        return self._get(f"/users/{username}/repos", params={"per_page": per_page})

    def get_content(self, owner: str, repo: str, path: str, ref: str | None = None) -> dict:
        """Содержимое файла или директории (Base64 для файлов)."""
        params: dict[str, str | int] = {"ref": ref} if ref else {}
        return self._get(f"/repos/{owner}/{repo}/contents/{path}", params=params)

    def get_file(self, owner: str, repo: str, path: str) -> dict:
        """Псевдоним get_content для совместимости с GitHubTool."""
        return self.get_content(owner, repo, path)

    # ── Issues ────────────────────────────────────────────────────────────────

    def list_issues(self, owner: str, repo: str,
                    state: str = "open", per_page: int = 20) -> list:
        """Список issues репозитория."""
        return self._get(
            f"/repos/{owner}/{repo}/issues",
            params={"state": state, "per_page": per_page},
        )

    def create_issue(self, owner: str, repo: str,
                     title: str, body: str = "",
                     labels: list[str] | None = None) -> dict:
        """Создаёт issue."""
        payload: dict[str, str | list[str]] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return self._post(f"/repos/{owner}/{repo}/issues", payload)

    def get_issue(self, owner: str, repo: str, number: int) -> dict:
        return self._get(f"/repos/{owner}/{repo}/issues/{number}")

    def comment_issue(self, owner: str, repo: str,
                      number: int, body: str) -> dict:
        return self._post(
            f"/repos/{owner}/{repo}/issues/{number}/comments",
            {"body": body},
        )

    # ── Pull Requests ─────────────────────────────────────────────────────────

    def create_pr(self, owner: str, repo: str, title: str,
                  head: str, base: str = "main", body: str = "") -> dict:
        """Создаёт Pull Request."""
        return self._post(
            f"/repos/{owner}/{repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body},
        )

    def list_prs(self, owner: str, repo: str,
                 state: str = "open") -> list:
        return self._get(
            f"/repos/{owner}/{repo}/pulls",
            params={"state": state},
        )

    # ── Поиск ─────────────────────────────────────────────────────────────────

    def search_code(self, query: str, per_page: int = 10) -> dict:
        """Поиск кода по GitHub."""
        return self._get(
            "/search/code",
            params={"q": query, "per_page": per_page},
        )

    def search_repos(self, query: str, sort: str = "stars",
                     per_page: int = 10) -> dict:
        return self._get(
            "/search/repositories",
            params={"q": query, "sort": sort, "per_page": per_page},
        )

    # ── Branches & Commits ────────────────────────────────────────────────────

    def list_branches(self, owner: str, repo: str) -> list:
        return self._get(f"/repos/{owner}/{repo}/branches")

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        return self._get(f"/repos/{owner}/{repo}/commits/{sha}")

    def list_commits(self, owner: str, repo: str,
                     branch: str | None = None, per_page: int = 10) -> list:
        params: dict[str, str | int] = {"per_page": per_page}
        if branch:
            params["sha"] = branch
        return self._get(f"/repos/{owner}/{repo}/commits", params=params)

    # ── Авторизованный пользователь ───────────────────────────────────────────

    def get_me(self) -> dict:
        return self._get("/user")

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict[str, str | int] | None = None):
        resp = self._requests.get(
            self.BASE + path,
            headers=self._headers(),
            params=params or {},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict):
        resp = self._requests.post(
            self.BASE + path,
            headers=self._headers(),
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

