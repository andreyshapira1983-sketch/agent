"""
VoiceCallTool — голосовые звонки и SMS.

Движки:
  1. Twilio  — https://www.twilio.com/try-twilio  (бесплатный trial $15)
  2. Vonage  — https://dashboard.nexmo.com/sign-up (бесплатные credits при регистрации)

Переменные окружения:
  # Twilio
  TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  TWILIO_FROM_NUMBER=+1234567890

  # Vonage (альтернатива)
  VONAGE_API_KEY=xxxxxxxx
  VONAGE_API_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  VONAGE_FROM_NUMBER=+1234567890
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from tools.tool_layer import BaseTool


class VoiceCallTool(BaseTool):
    name = 'voice_call'
    description = (
        'Голосовые звонки и SMS через Twilio или Vonage: '
        'исходящий звонок с TTS-сообщением, отправка SMS, '
        'история звонков и сообщений. '
        'Twilio: бесплатный trial $15 на https://www.twilio.com/try-twilio'
    )

    TWILIO_BASE  = 'https://api.twilio.com/2010-04-01'
    VONAGE_SMS   = 'https://rest.nexmo.com/sms/json'

    def __init__(self,
                 twilio_sid: str | None = None,
                 twilio_token: str | None = None,
                 twilio_from: str | None = None,
                 vonage_key: str | None = None,
                 vonage_secret: str | None = None,
                 vonage_from: str | None = None):
        self.twilio_sid   = twilio_sid   or os.environ.get('TWILIO_ACCOUNT_SID', '')
        self.twilio_token = twilio_token or os.environ.get('TWILIO_AUTH_TOKEN', '')
        self.twilio_from  = twilio_from  or os.environ.get('TWILIO_FROM_NUMBER', '')
        self.vonage_key    = vonage_key    or os.environ.get('VONAGE_API_KEY', '')
        self.vonage_secret = vonage_secret or os.environ.get('VONAGE_API_SECRET', '')
        self.vonage_from   = vonage_from   or os.environ.get('VONAGE_FROM_NUMBER', '')

    # ─── Twilio helpers ──────────────────────────────────────────────────────

    def _twilio(self, path: str, data: dict | None = None) -> dict:
        if not self.twilio_sid or not self.twilio_token:
            return {
                'ok': False,
                'error': 'TWILIO_ACCOUNT_SID или TWILIO_AUTH_TOKEN не заданы.',
                'register': 'Зарегистрируйся бесплатно: https://www.twilio.com/try-twilio',
            }
        url = f'{self.TWILIO_BASE}/Accounts/{self.twilio_sid}{path}'
        creds = base64.b64encode(
            f'{self.twilio_sid}:{self.twilio_token}'.encode()
        ).decode()
        headers = {
            'Authorization': f'Basic {creds}',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        body = urllib.parse.urlencode(data).encode() if data else None
        req = urllib.request.Request(
            url, data=body, headers=headers,
            method='POST' if data else 'GET',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return {'ok': True, **json.loads(r.read().decode())}
        except urllib.error.HTTPError as e:
            return {'ok': False, 'status': e.code, 'error': e.read().decode(errors='replace')}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── Twilio методы ───────────────────────────────────────────────────────

    def twilio_call(self, to: str, message: str,
                    from_number: str | None = None,
                    language: str = 'ru-RU') -> dict:
        """
        Исходящий голосовой звонок через Twilio с TTS-сообщением.
        to/from — номера E.164: +79991234567
        message — текст, который робот произнесёт.
        """
        fr = from_number or self.twilio_from
        twiml = f'<Response><Say language="{language}">{message}</Say></Response>'
        return self._twilio('/Calls.json', {'To': to, 'From': fr, 'Twiml': twiml})

    def twilio_sms(self, to: str, message: str,
                   from_number: str | None = None) -> dict:
        """Отправить SMS через Twilio."""
        fr = from_number or self.twilio_from
        return self._twilio('/Messages.json', {'To': to, 'From': fr, 'Body': message})

    def twilio_list_calls(self, limit: int = 20) -> dict:
        """История последних звонков."""
        return self._twilio(f'/Calls.json?PageSize={limit}')

    def twilio_list_sms(self, limit: int = 20) -> dict:
        """История последних SMS."""
        return self._twilio(f'/Messages.json?PageSize={limit}')

    def twilio_call_status(self, call_sid: str) -> dict:
        """Статус конкретного звонка по SID."""
        return self._twilio(f'/Calls/{call_sid}.json')

    # ─── Vonage методы ───────────────────────────────────────────────────────

    def vonage_sms(self, to: str, message: str,
                   from_name: str | None = None) -> dict:
        """
        Отправить SMS через Vonage (Nexmo).
        Бесплатные credits при регистрации: https://dashboard.nexmo.com/sign-up
        """
        if not self.vonage_key or not self.vonage_secret:
            return {
                'ok': False,
                'error': 'VONAGE_API_KEY / VONAGE_API_SECRET не заданы.',
                'register': 'https://dashboard.nexmo.com/sign-up',
            }
        data = urllib.parse.urlencode({
            'api_key':    self.vonage_key,
            'api_secret': self.vonage_secret,
            'to':         to,
            'from':       from_name or self.vonage_from or 'Agent',
            'text':       message,
        }).encode()
        req = urllib.request.Request(
            self.VONAGE_SMS, data=data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return {'ok': True, **json.loads(r.read().decode())}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ─── run() dispatcher ────────────────────────────────────────────────────

    def run(self, action: str = 'twilio_sms', **params) -> dict:
        """
        action:
          twilio_call | twilio_sms | twilio_list_calls | twilio_list_sms |
          twilio_call_status | vonage_sms
        """
        actions = {
            'twilio_call':        self.twilio_call,
            'twilio_sms':         self.twilio_sms,
            'twilio_list_calls':  self.twilio_list_calls,
            'twilio_list_sms':    self.twilio_list_sms,
            'twilio_call_status': self.twilio_call_status,
            'vonage_sms':         self.vonage_sms,
        }
        fn = actions.get(action)
        if not fn:
            return {'ok': False, 'error': f'Неизвестный action: {action}. Доступные: {list(actions)}'}
        try:
            return fn(**params) or {'ok': True}
        except TypeError as e:
            return {'ok': False, 'error': f'Неверные параметры: {e}'}
