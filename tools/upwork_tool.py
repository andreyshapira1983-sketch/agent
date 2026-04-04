# Upwork Tool — инструмент Tool Layer (Слой 5)
# Архитектура автономного AI-агента
# Upwork GraphQL API + OAuth 2.0
# Docs: https://developers.upwork.com/
# pylint: disable=broad-except

from __future__ import annotations

import json
import time
import threading

from tools.tool_layer import BaseTool


_GRAPHQL_URL = "https://api.upwork.com/graphql"
_AUTH_URL    = "https://www.upwork.com/ab/account-security/oauth2/authorize"
_TOKEN_URL   = "https://www.upwork.com/api/v3/oauth2/token"


class UpworkTool(BaseTool):
    """
    Upwork GraphQL API Tool — поиск заказов, профиль, подача заявок.

    Требует OAuth 2.0 credentials:
        client_id      — Consumer Key из Upwork Developer Portal
        client_secret  — Consumer Secret
        access_token   — OAuth 2.0 Access Token
        refresh_token  — OAuth 2.0 Refresh Token (для автообновления)

    ─── КАК ПОЛУЧИТЬ ─────────────────────────────────────────────────────────
    1. https://www.upwork.com/services/api/apply → зарегистрируй приложение
       → получи Consumer Key (= Client ID) и Consumer Secret
    2. Заполни .env: UPWORK_CLIENT_ID=...  UPWORK_CLIENT_SECRET=...
    3. Запусти: python -m tools.upwork_tool auth
       → скопируй URL → авторизуйся → введи code
       → токены сохранятся в .env автоматически

    ─── МЕТОДЫ ───────────────────────────────────────────────────────────────
        search_jobs(query, count)    — найти заказы
        get_job(job_id)              — детали заказа
        get_my_profile()             — мой профиль
        get_proposals(status)        — мои заявки
        submit_proposal(...)         — подать заявку
        run(action, **params)        — универсальный вызов (для агента)
    """

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        monitoring=None,
    ):
        super().__init__('upwork', 'Upwork GraphQL API: поиск заказов, подача заявок, профиль')
        self._client_id     = client_id or ''
        self._client_secret = client_secret or ''
        self._access_token  = access_token or ''
        self._refresh_token = refresh_token or ''
        self._token_expires = 0.0
        self._lock = threading.Lock()
        self.monitoring = monitoring

    @property
    def is_ready(self) -> bool:
        """True если client_id и access_token заданы."""
        return bool(self._client_id and self._access_token)

    # ── Поиск заказов ─────────────────────────────────────────────────────────

    def search_jobs(self, query: str, count: int = 10, offset: int = 0) -> dict:
        """
        Поиск заказов через Upwork GraphQL API.

        Args:
            query  — ключевые слова, например "AI Agents Python"
            count  — количество результатов (макс 50)
            offset — смещение для пагинации
        """
        if not self.is_ready:
            return self._not_ready()

        gql = """
        query SearchJobs($query: String!, $paging: PagingInput) {
          marketplaceJobPostings(
            searchQuery: $query
            paging: $paging
          ) {
            totalCount
            edges {
              node {
                id
                title
                description
                createdDateTime
                jobType
                budget { amount currency }
                skills { name }
              }
            }
          }
        }
        """
        variables = {
            'query':  query,
            'paging': {'limit': min(count, 50), 'offset': offset},
        }
        data = self._query(gql, variables)
        if 'error' in data:
            return data

        raw   = (data.get('data') or {}).get('marketplaceJobPostings', {})
        edges = raw.get('edges', [])
        jobs  = []
        for edge in edges:
            node   = edge.get('node', {})
            budget = node.get('budget') or {}
            skills = [s.get('name', '') for s in (node.get('skills') or [])]
            jobs.append({
                'id':           node.get('id', ''),
                'title':        node.get('title', ''),
                'snippet':      (node.get('description') or '')[:300],
                'budget':       f"{budget.get('amount', '?')} {budget.get('currency', 'USD')}",
                'job_type':     node.get('jobType', ''),
                'skills':       skills,
                'date_created': node.get('createdDateTime', ''),
                'url':          f"https://www.upwork.com/jobs/{node.get('id', '')}",
            })

        return {
            'jobs':        jobs,
            'total_count': raw.get('totalCount', len(jobs)),
            'query':       query,
        }

    # ── Детали заказа ─────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> dict:
        """Подробное описание заказа по ID."""
        if not self.is_ready:
            return self._not_ready()
        gql = """
        query GetJob($id: ID!) {
          jobPosting(id: $id) {
            id title description createdDateTime jobType
            budget { amount currency }
            skills { name }
            client { totalFeedback totalPostedJobs country { name } }
          }
        }
        """
        data = self._query(gql, {'id': job_id})
        return (data.get('data') or {}).get('jobPosting', data)

    # ── Мой профиль ──────────────────────────────────────────────────────────

    def get_my_profile(self) -> dict:
        """Профиль текущего аутентифицированного фрилансера."""
        if not self.is_ready:
            return self._not_ready()
        gql = """
        query {
          currentUser {
            id
            name
            freelancerProfile {
              title
              overview
              hourlyRate { amount currency }
              skills { name }
              totalFeedback
              totalHours
              jobSuccessScore
              topRatedStatus
            }
          }
        }
        """
        data = self._query(gql, {})
        return (data.get('data') or {}).get('currentUser', data)

    # ── Заявки ────────────────────────────────────────────────────────────────

    def get_proposals(self, status: str = 'active') -> dict:
        """
        Список поданных заявок.

        Args:
            status — 'active', 'archived', 'declined'
        """
        if not self.is_ready:
            return self._not_ready()
        gql = """
        query GetProposals($status: ProposalStatusType) {
          myProposals(status: $status) {
            totalCount
            edges {
              node {
                id
                coverLetter
                chargeRate { amount currency }
                status
                job { id title }
                createdDateTime
              }
            }
          }
        }
        """
        data = self._query(gql, {'status': status.upper()})
        raw   = (data.get('data') or {}).get('myProposals', {})
        edges = raw.get('edges', [])
        proposals = []
        for edge in edges:
            node = edge.get('node', {})
            job  = node.get('job') or {}
            rate = node.get('chargeRate') or {}
            proposals.append({
                'id':           node.get('id', ''),
                'job_id':       job.get('id', ''),
                'job_title':    job.get('title', ''),
                'cover_letter': (node.get('coverLetter') or '')[:200],
                'bid':          f"{rate.get('amount', '?')} {rate.get('currency', 'USD')}",
                'status':       node.get('status', ''),
                'date':         node.get('createdDateTime', ''),
            })
        return {'proposals': proposals, 'total': raw.get('totalCount', len(proposals))}

    def submit_proposal(
        self,
        job_id: str,
        cover_letter: str,
        bid_amount: float,
        bid_currency: str = 'USD',
    ) -> dict:
        """
        Подать заявку на заказ через GraphQL mutation.

        Args:
            job_id        — ID заказа (из search_jobs)
            cover_letter  — сопроводительное письмо
            bid_amount    — ставка в $
            bid_currency  — валюта (по умолчанию USD)
        """
        if not self.is_ready:
            return self._not_ready()
        gql = """
        mutation SubmitProposal($input: SubmitProposalInput!) {
          submitProposal(input: $input) {
            proposal {
              id
              status
              createdDateTime
            }
          }
        }
        """
        variables = {
            'input': {
                'jobId':       job_id,
                'coverLetter': cover_letter,
                'chargeRate': {
                    'amount':   bid_amount,
                    'currency': bid_currency,
                },
            }
        }
        data = self._query(gql, variables)
        if 'error' in data:
            return data
        proposal = ((data.get('data') or {}).get('submitProposal') or {}).get('proposal', {})
        if proposal.get('id'):
            self._log(f"Заявка подана: {proposal['id']} статус={proposal.get('status')}")
            return {'success': True, 'proposal_id': proposal['id'], 'status': proposal.get('status')}
        errors = data.get('errors', [])
        return {'error': '; '.join(e.get('message', '') for e in errors) or 'Неизвестная ошибка'}

    # ── Универсальный run() для агента ────────────────────────────────────────

    def run(self, *args, **kwargs) -> dict:
        """
        Универсальный интерфейс для агента.

        Примеры:
            UPWORK:search_jobs query="AI Agents Python" count=10
            UPWORK:get_job job_id="~017..."
            UPWORK:get_my_profile
            UPWORK:get_proposals status=active
            UPWORK:submit_proposal job_id="~017..." cover_letter="..." bid_amount=150
        """
        action: str = args[0] if args else kwargs.pop('action', 'search_jobs')
        if not self.is_ready:
            return self._not_ready()
        dispatch = {
            'search_jobs':     self.search_jobs,
            'get_job':         self.get_job,
            'get_my_profile':  self.get_my_profile,
            'get_proposals':   self.get_proposals,
            'submit_proposal': self.submit_proposal,
        }
        handler = dispatch.get(action)
        if not handler:
            return {'error': f"Неизвестное действие: '{action}'. Доступные: {list(dispatch.keys())}"}
        return handler(**kwargs)

    # ── GraphQL helper ────────────────────────────────────────────────────────

    def _query(self, query: str, variables: dict) -> dict:
        """Выполняет GraphQL запрос с автообновлением токена."""
        self._maybe_refresh_token()
        try:
            import urllib.request as _req
            payload = json.dumps({'query': query, 'variables': variables}).encode()
            request = _req.Request(
                _GRAPHQL_URL,
                data=payload,
                headers={
                    'Authorization': f'Bearer {self._access_token}',
                    'Content-Type':  'application/json',
                    'Accept':        'application/json',
                },
                method='POST',
            )
            with _req.urlopen(request, timeout=20) as resp:
                body = resp.read().decode('utf-8', errors='replace')
            result = json.loads(body)
            if not isinstance(result, dict):
                return {'error': f'Unexpected response type: {type(result).__name__}'}
            if 'errors' in result and not result.get('data'):
                msgs = '; '.join(e.get('message', '') for e in result['errors'])
                return {'error': f'GraphQL: {msgs}'}
            return result
        except Exception as exc:
            return {'error': str(exc)}

    # ── OAuth 2.0: автообновление токена ─────────────────────────────────────

    def _maybe_refresh_token(self):
        if not self._refresh_token:
            return
        if time.time() < self._token_expires - 60:
            return
        with self._lock:
            if time.time() < self._token_expires - 60:
                return
            self._do_refresh()

    def _do_refresh(self):
        try:
            import urllib.request as _req
            import urllib.parse as _parse
            import base64
            credentials = base64.b64encode(
                f"{self._client_id}:{self._client_secret}".encode()
            ).decode()
            data = _parse.urlencode({
                'grant_type':    'refresh_token',
                'refresh_token': self._refresh_token,
            }).encode()
            request = _req.Request(
                _TOKEN_URL, data=data,
                headers={
                    'Authorization': f'Basic {credentials}',
                    'Content-Type':  'application/x-www-form-urlencoded',
                },
                method='POST',
            )
            with _req.urlopen(request, timeout=15) as resp:
                body = json.loads(resp.read().decode())
            self._access_token  = body['access_token']
            self._refresh_token = body.get('refresh_token', self._refresh_token)
            self._token_expires = time.time() + body.get('expires_in', 86400)
            _update_env('.env', self._access_token, self._refresh_token)
            self._log("Access token обновлён.")
        except Exception as exc:
            self._log(f"Ошибка обновления токена: {exc}", level='error')

    def _not_ready(self) -> dict:
        return {
            'error': (
                'Upwork credentials не заданы. '
                'Заполни .env: UPWORK_CLIENT_ID, UPWORK_CLIENT_SECRET, '
                'UPWORK_ACCESS_TOKEN, UPWORK_REFRESH_TOKEN. '
                'Инструкция: python -m tools.upwork_tool auth'
            )
        }

    def _log(self, message: str, level: str = 'info'):
        if self.monitoring:
            if level == 'error':
                self.monitoring.error(message, source='upwork_tool')
            else:
                self.monitoring.info(message, source='upwork_tool')
        else:
            print(f"[UpworkTool] {message}")


# ── OAuth 2.0 авторизация (один раз: python -m tools.upwork_tool auth) ────────

def run_oauth_flow(client_id: str, client_secret: str, env_path: str = '.env'):
    """
    Интерактивный OAuth 2.0 Authorization Code flow.
    Запускать один раз. Сохраняет токены в .env.
    """
    import urllib.parse as _parse
    import urllib.request as _req
    import base64

    redirect_uri = 'https://your.callback.url'

    auth_params = _parse.urlencode({
        'response_type': 'code',
        'client_id':     client_id,
        'redirect_uri':  redirect_uri,
    })
    auth_link = f"{_AUTH_URL}?{auth_params}"

    print("\n=== Upwork OAuth 2.0 авторизация ===\n")
    print(f"1. Открой в браузере:\n   {auth_link}\n")
    print("2. Авторизуйся под своим аккаунтом Upwork.")
    print("3. Тебя перенаправит на URL вида:")
    print("   https://your.callback.url?code=XXXX")
    print("   Скопируй значение параметра 'code'.\n")
    code = input("Введи code: ").strip()

    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    data = _parse.urlencode({
        'grant_type':   'authorization_code',
        'code':         code,
        'redirect_uri': redirect_uri,
    }).encode()
    request = _req.Request(
        _TOKEN_URL, data=data,
        headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type':  'application/x-www-form-urlencoded',
        },
        method='POST',
    )
    try:
        with _req.urlopen(request, timeout=20) as resp:
            tokens = json.loads(resp.read().decode())
    except Exception as exc:
        print(f"Ошибка при получении токенов: {exc}")
        return

    if 'error' in tokens:
        print(f"Upwork вернул ошибку: {tokens.get('error_description', tokens)}")
        return

    access_token  = tokens['access_token']
    refresh_token = tokens.get('refresh_token', '')
    expires_in    = tokens.get('expires_in', 86400)

    _update_env(env_path, access_token, refresh_token)

    print(f"\nУспех! Токены сохранены в {env_path}")
    print("  access_token  : ***")
    print("  refresh_token : ***" if refresh_token else "  (refresh_token не выдан)")
    print(f"  expires_in    : {expires_in} сек ({expires_in // 3600} ч)")


def _update_env(env_path: str, access_token: str, refresh_token: str):
    """Обновляет UPWORK_ACCESS_TOKEN и UPWORK_REFRESH_TOKEN в .env."""
    import re
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        content = ''

    def _set(text: str, key: str, value: str) -> str:
        pattern = rf'^{re.escape(key)}=.*$'
        replacement = f'{key}={value}'
        if re.search(pattern, text, re.MULTILINE):
            return re.sub(pattern, replacement, text, flags=re.MULTILINE)
        return text.rstrip('\n') + f'\n{replacement}\n'

    content = _set(content, 'UPWORK_ACCESS_TOKEN', access_token)
    if refresh_token:
        content = _set(content, 'UPWORK_REFRESH_TOKEN', refresh_token)

    with open(env_path, 'w', encoding='utf-8') as f:
        f.write(content)


if __name__ == '__main__':
    import os
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == 'auth':
        ck = os.environ.get('UPWORK_CLIENT_ID') or input("Client ID (Consumer Key): ").strip()
        cs = os.environ.get('UPWORK_CLIENT_SECRET') or input("Client Secret: ").strip()
        run_oauth_flow(ck, cs)
    else:
        print("Использование: python -m tools.upwork_tool auth")
