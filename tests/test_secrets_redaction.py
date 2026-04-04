# Tests for Secrets Redaction Middleware
# communication/secrets_redaction.py + integrations

from __future__ import annotations

import json
import os
import tempfile
import threading

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from communication.secrets_redaction import SecretsRedactor, _PLACEHOLDER, _is_sensitive_key


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SecretsRedactor — core
# ═══════════════════════════════════════════════════════════════════════════════


class TestRedactorBasic:
    """Базовая функциональность SecretsRedactor."""

    def test_empty_input(self):
        r = SecretsRedactor()
        assert r.scrub_text('') == ''
        assert r.scrub_text(None) is None  # type: ignore[arg-type]

    def test_no_secrets(self):
        r = SecretsRedactor()
        assert r.scrub_text('Hello World') == 'Hello World'

    def test_callable_interface(self):
        r = SecretsRedactor(extra_values=['my-secret-value-1234'])
        result = r('text with my-secret-value-1234 inside')
        assert 'my-secret-value-1234' not in result
        assert _PLACEHOLDER in result


class TestPatternBased:
    """Pattern-based секреты (regex)."""

    def test_openai_key(self):
        r = SecretsRedactor()
        text = 'key=sk-1234567890abcdefABCDEF1234567890'
        result = r.scrub_text(text)
        assert 'sk-1234567890' not in result

    def test_anthropic_key(self):
        r = SecretsRedactor()
        text = 'ANTHROPIC_API_KEY=sk-ant-abcd1234567890ABCDEF1234'
        result = r.scrub_text(text)
        assert 'sk-ant-' not in result

    def test_github_pat(self):
        r = SecretsRedactor()
        text = 'token: ghp_ABC123456789012345678901234567890123'
        result = r.scrub_text(text)
        assert 'ghp_' not in result

    def test_github_fine_grained(self):
        r = SecretsRedactor()
        text = 'github_pat_ABCDEF1234567890ABCDEF'
        result = r.scrub_text(text)
        assert 'github_pat_' not in result

    def test_hf_token(self):
        r = SecretsRedactor()
        text = 'HF_TOKEN=hf_abcdefghijklmnopqrstuv'
        result = r.scrub_text(text)
        assert 'hf_' not in result

    def test_telegram_bot_token(self):
        r = SecretsRedactor()
        text = 'bot 1234567890:ABCDEFghijklmnop_qrstuvwxyz12345678'
        result = r.scrub_text(text)
        assert '1234567890:' not in result

    def test_bearer_token(self):
        r = SecretsRedactor()
        text = 'Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test'
        result = r.scrub_text(text)
        assert 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' not in result

    def test_aws_key(self):
        r = SecretsRedactor()
        text = 'aws_key=AKIAIOSFODNN7EXAMPLE'
        result = r.scrub_text(text)
        assert 'AKIAIOSFODNN7EXAMPLE' not in result

    def test_slack_token(self):
        r = SecretsRedactor()
        text = 'SLACK=xoxb-123456789-abcdefgh'
        result = r.scrub_text(text)
        assert 'xoxb-' not in result

    def test_plain_text_untouched(self):
        """Обычный текст без паттернов секретов не изменяется."""
        r = SecretsRedactor()
        text = 'This is a normal log message about task completion.'
        assert r.scrub_text(text) == text


class TestValueBased:
    """Value-based секреты через SecuritySystem и extra_values."""

    def test_extra_values(self):
        r = SecretsRedactor(extra_values=['SuperSecret1234', 'AnotherVal5678'])
        text = 'key=SuperSecret1234 and extra=AnotherVal5678 done'
        result = r.scrub_text(text)
        assert 'SuperSecret1234' not in result
        assert 'AnotherVal5678' not in result
        assert _PLACEHOLDER in result

    def test_extra_values_short_ignored(self):
        """Значения < 4 символов не удаляются (ложные срабатывания)."""
        r = SecretsRedactor(extra_values=['abc', 'xy', ''])
        assert r.scrub_text('abc xy test') == 'abc xy test'

    def test_security_system_integration(self):
        """SecuritySystem.scrub_text используется если передан."""
        class MockSecurity:
            def scrub_text(self, text):
                return text.replace('REAL_SECRET_abcd', '***')
        r = SecretsRedactor(security=MockSecurity())
        result = r.scrub_text('val=REAL_SECRET_abcd')
        assert 'REAL_SECRET_abcd' not in result

    def test_add_values_runtime(self):
        r = SecretsRedactor()
        r.add_values(['RuntimeSecret1234'])
        result = r.scrub_text('found RuntimeSecret1234 here')
        assert 'RuntimeSecret1234' not in result


class TestScrubDict:
    """scrub_dict — рекурсивная очистка структур."""

    def test_flat_dict_sensitive_keys(self):
        r = SecretsRedactor()
        data = {'user': 'alice', 'password': 'hunter2', 'api_key': 'sk123'}
        result = r.scrub_dict(data)
        assert result['user'] == 'alice'
        assert result['password'] == _PLACEHOLDER
        assert result['api_key'] == _PLACEHOLDER

    def test_nested_dict(self):
        r = SecretsRedactor()
        data = {'config': {'token': 'abc123', 'host': 'localhost'}}
        result = r.scrub_dict(data)
        assert result['config']['token'] == _PLACEHOLDER
        assert result['config']['host'] == 'localhost'

    def test_list_values(self):
        r = SecretsRedactor(extra_values=['SecretVal1234'])
        data = {'items': ['normal', 'has SecretVal1234 inside']}
        result = r.scrub_dict(data)
        assert 'SecretVal1234' not in result['items'][1]

    def test_non_string_values_untouched(self):
        r = SecretsRedactor()
        data = {'count': 42, 'active': True, 'tags': None}
        result = r.scrub_dict(data)
        assert result == data

    def test_mixed_sensitive_non_string(self):
        """Sensitive key с non-string значением — оставляем as-is."""
        r = SecretsRedactor()
        data = {'token': 12345}
        result = r.scrub_dict(data)
        assert result['token'] == 12345  # int, not scrubbed


class TestScrubException:
    """scrub_exception — очистка traceback."""

    def test_exception_scrubbed(self):
        r = SecretsRedactor(extra_values=['MySecret1234'])
        result = ''
        try:
            raise ValueError('Connection failed with MySecret1234')
        except ValueError as e:
            result = r.scrub_exception(e)
        assert 'MySecret1234' not in result
        assert 'ValueError' in result
        assert _PLACEHOLDER in result


class TestIsSensitiveKey:
    """_is_sensitive_key helper."""

    def test_sensitive_keys(self):
        for key in ['password', 'API_KEY', 'access_token', 'client_secret',
                     'Authorization', 'CREDENTIAL']:
            assert _is_sensitive_key(key), f'{key} should be sensitive'

    def test_non_sensitive_keys(self):
        for key in ['user', 'name', 'host', 'port', 'count']:
            assert not _is_sensitive_key(key), f'{key} should not be sensitive'


# ═══════════════════════════════════════════════════════════════════════════════
# 2. ImmutableAuditLog integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestImmutableAuditLogScrubbing:
    """ImmutableAuditLog с scrubber убирает секреты из data."""

    def test_record_with_scrubber(self, tmp_path):
        from safety.hardening import ImmutableAuditLog
        log_path = str(tmp_path / 'audit.jsonl')
        redactor = SecretsRedactor(extra_values=['TopSecret1234'])
        log = ImmutableAuditLog(log_path, scrubber=redactor)

        log.record('test_event', {'message': 'key=TopSecret1234', 'user': 'bob'})

        with open(log_path, 'r', encoding='utf-8') as f:
            entry = json.loads(f.readline())
        assert 'TopSecret1234' not in entry['data']['message']
        assert _PLACEHOLDER in entry['data']['message']
        assert entry['data']['user'] == 'bob'

    def test_record_without_scrubber(self, tmp_path):
        from safety.hardening import ImmutableAuditLog
        log_path = str(tmp_path / 'audit.jsonl')
        log = ImmutableAuditLog(log_path)  # no scrubber

        log.record('test_event', {'message': 'plain text'})

        with open(log_path, 'r', encoding='utf-8') as f:
            entry = json.loads(f.readline())
        assert entry['data']['message'] == 'plain text'

    def test_integrity_preserved_with_scrubbing(self, tmp_path):
        from safety.hardening import ImmutableAuditLog
        log_path = str(tmp_path / 'audit.jsonl')
        redactor = SecretsRedactor(extra_values=['Secret9999'])
        log = ImmutableAuditLog(log_path, scrubber=redactor)

        log.record('event1', {'info': 'Secret9999 leaked'})
        log.record('event2', {'info': 'clean data'})

        ok, checked, detail = log.verify_integrity()
        assert ok, f'integrity check failed: {detail}'
        assert checked == 2

    def test_nested_dict_scrubbed(self, tmp_path):
        from safety.hardening import ImmutableAuditLog
        log_path = str(tmp_path / 'audit.jsonl')
        redactor = SecretsRedactor(extra_values=['NestedSecret1234'])
        log = ImmutableAuditLog(log_path, scrubber=redactor)

        log.record('nested', {'config': {'key': 'NestedSecret1234'}})

        with open(log_path, 'r', encoding='utf-8') as f:
            entry = json.loads(f.readline())
        assert 'NestedSecret1234' not in json.dumps(entry)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CommandGateway integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommandGatewayScrubbing:
    """CommandGateway._audit использует scrubber для command."""

    def test_audit_scrubs_command(self, tmp_path):
        from execution.command_gateway import CommandGateway, GatewayResult
        audit_path = str(tmp_path / 'commands.jsonl')
        gw = CommandGateway(audit_log=audit_path)
        gw._scrubber = SecretsRedactor(extra_values=['CmdSecret1234'])  # type: ignore[attr-defined]

        result = GatewayResult(
            allowed=True,
            command='echo CmdSecret1234',
            exe='echo',
        )
        gw._audit(result, caller='test')

        with open(audit_path, 'r', encoding='utf-8') as f:
            entry = json.loads(f.readline())
        assert 'CmdSecret1234' not in entry['command']
        assert _PLACEHOLDER in entry['command']

    def test_audit_no_scrubber(self, tmp_path):
        from execution.command_gateway import CommandGateway, GatewayResult
        audit_path = str(tmp_path / 'commands.jsonl')
        gw = CommandGateway(audit_log=audit_path)

        result = GatewayResult(
            allowed=True,
            command='echo hello',
            exe='echo',
        )
        gw._audit(result, caller='test')

        with open(audit_path, 'r', encoding='utf-8') as f:
            entry = json.loads(f.readline())
        assert entry['command'] == 'echo hello'


# ═══════════════════════════════════════════════════════════════════════════════
# 4. TelegramSink integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestTelegramSinkScrubbing:
    """TelegramSink._format использует _scrubber."""

    def test_format_scrubs_secrets(self):
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.token = 'fake_token_1234'
        sink._scrubber = SecretsRedactor(extra_values=['SinkSecret1234'])  # type: ignore[assignment]

        entry = {
            'level': 'ERROR',
            'source': 'test',
            'message': 'failed with SinkSecret1234',
            'time_str': '12:00',
        }
        text = sink._format(entry)
        assert 'SinkSecret1234' not in text
        assert _PLACEHOLDER in text

    def test_format_without_scrubber(self):
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.token = ''
        sink._scrubber = None

        entry = {
            'level': 'INFO',
            'source': 'test',
            'message': 'normal message',
            'time_str': '12:00',
        }
        text = sink._format(entry)
        assert 'normal message' in text

    def test_token_still_scrubbed(self):
        """Собственный токен sink удаляется даже без SecretsRedactor."""
        from llm.telegram_sink import TelegramSink
        sink = TelegramSink.__new__(TelegramSink)
        sink.token = '999888777:ABCDEFghijklmnop_qrstuvwxyz1234567'
        sink._scrubber = None

        entry = {
            'level': 'ERROR',
            'source': 'x',
            'message': f'err {sink.token}',
            'time_str': '',
        }
        text = sink._format(entry)
        assert sink.token not in text


# ═══════════════════════════════════════════════════════════════════════════════
# 5. response_sanitizer integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestResponseSanitizer:
    """scrub_response / set_response_redactor."""

    def test_set_and_use_redactor(self):
        from communication.response_sanitizer import set_response_redactor, scrub_response
        r = SecretsRedactor(extra_values=['ResponseSecret1234'])
        set_response_redactor(r)
        try:
            result = scrub_response('answer is ResponseSecret1234')
            assert 'ResponseSecret1234' not in result
            assert _PLACEHOLDER in result
        finally:
            set_response_redactor(None)

    def test_no_redactor_passthrough(self):
        from communication.response_sanitizer import set_response_redactor, scrub_response
        set_response_redactor(None)
        assert scrub_response('plain') == 'plain'


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Thread safety
# ═══════════════════════════════════════════════════════════════════════════════


class TestConcurrency:
    """SecretsRedactor thread-safe (stateless reads)."""

    def test_concurrent_scrub(self):
        r = SecretsRedactor(extra_values=['ConcurrentSecret1234'])
        results: list[str] = []
        errors: list[str] = []

        def worker():
            try:
                res = r.scrub_text('data=ConcurrentSecret1234 end')
                results.append(res)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert not errors
        assert len(results) == 10
        for res in results:
            assert 'ConcurrentSecret1234' not in res
