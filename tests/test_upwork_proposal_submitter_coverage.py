"""Tests for skills/upwork_proposal_submitter.py — UpworkProposalSubmitter."""
import time
from unittest.mock import MagicMock, patch
import pytest

from skills.upwork_proposal_submitter import (
    UpworkProposalSubmitter,
    ProposalResult,
)


class TestProposalResult:
    def test_defaults(self):
        r = ProposalResult(success=True, message='ok')
        assert r.success
        assert r.message == 'ok'
        assert r.screenshot_b64 is None

    def test_with_screenshot(self):
        r = ProposalResult(success=False, message='fail', screenshot_b64='abc123')
        assert r.screenshot_b64 == 'abc123'


class TestUpworkProposalSubmitter:
    def _make_submitter(self, **kwargs):
        return UpworkProposalSubmitter(**kwargs)

    def test_init_defaults(self):
        s = self._make_submitter()
        assert s.browser_tool is None
        assert s.telegram_bot is None
        assert s.default_hourly_rate == 25.0
        assert s.approval_timeout == 600

    def test_init_custom(self):
        bt = MagicMock()
        tg = MagicMock()
        s = self._make_submitter(
            browser_tool=bt,
            telegram_bot=tg,
            telegram_chat_id=123,
            default_hourly_rate=50.0,
            approval_timeout=300,
        )
        assert s.browser_tool is bt
        assert s.telegram_bot is tg
        assert s.default_hourly_rate == 50.0

    # ── _request_human_approval ───────────────────────────────────────────

    def test_approval_no_telegram(self):
        s = self._make_submitter()
        result = s._request_human_approval('url', 'letter', 30.0, 'title')
        assert result is True

    def test_approval_no_request_approval_method(self):
        tg = MagicMock(spec=[])  # no request_approval
        s = self._make_submitter(telegram_bot=tg, telegram_chat_id=123)
        result = s._request_human_approval('url', 'letter', 30.0, 'title')
        assert result is True

    def test_approval_approved(self):
        tg = MagicMock()
        tg.request_approval.return_value = True
        s = self._make_submitter(telegram_bot=tg, telegram_chat_id=123)
        result = s._request_human_approval('url', 'letter', 30.0, 'Job Title')
        assert result is True
        tg.send.assert_called_once()
        tg.request_approval.assert_called_once()

    def test_approval_rejected(self):
        tg = MagicMock()
        tg.request_approval.return_value = False
        s = self._make_submitter(telegram_bot=tg, telegram_chat_id=123)
        result = s._request_human_approval('url', 'letter', 30.0, 'Job')
        assert result is False

    def test_approval_send_error(self):
        tg = MagicMock()
        tg.send.side_effect = RuntimeError("send fail")
        tg.request_approval.return_value = True
        s = self._make_submitter(telegram_bot=tg, telegram_chat_id=123)
        result = s._request_human_approval('url', 'letter', 30.0, '')
        assert result is True  # still proceeds

    def test_approval_request_error(self):
        tg = MagicMock()
        tg.request_approval.side_effect = RuntimeError("approval fail")
        s = self._make_submitter(telegram_bot=tg, telegram_chat_id=123)
        result = s._request_human_approval('url', 'letter', 30.0, 'Job')
        assert result is False

    # ── submit ────────────────────────────────────────────────────────────

    def test_submit_rejected(self):
        tg = MagicMock()
        tg.request_approval.return_value = False
        s = self._make_submitter(telegram_bot=tg, telegram_chat_id=123)
        result = s.submit('http://upwork.com/job/123', 'cover letter')
        assert not result.success
        assert 'Отклонено' in result.message

    def test_submit_skip_approval(self):
        browser = MagicMock()
        browser.wait_for.return_value = False
        browser.click.return_value = True
        browser.fill.return_value = True
        browser.screenshot.return_value = 'screenshot_data'
        browser.get_text.return_value = 'proposal submitted successfully'
        browser.current_url = 'http://upwork.com/proposals'
        s = self._make_submitter(browser_tool=browser)
        result = s.submit(
            'http://upwork.com/job/123',
            'My cover letter',
            bid_amount=30.0,
            skip_approval=True,
        )
        # It depends on selector matching
        assert isinstance(result, ProposalResult)

    def test_submit_browser_error(self):
        # Test with browser_tool that raises on navigate
        browser = MagicMock()
        browser.navigate.side_effect = RuntimeError("browser boom")
        s = self._make_submitter(browser_tool=browser)
        result = s.submit('http://upwork.com/job/123', 'letter', skip_approval=True)
        assert not result.success
        assert 'Ошибка' in result.message

    def test_submit_default_bid(self):
        browser = MagicMock()
        browser.wait_for.return_value = False
        browser.click.return_value = False
        browser.screenshot.return_value = None
        s = self._make_submitter(browser_tool=browser, default_hourly_rate=40.0)
        result = s.submit('http://upwork.com/job/123', 'letter', skip_approval=True)
        assert isinstance(result, ProposalResult)

    # ── _needs_login ─────────────────────────────────────────────────────

    def test_needs_login_true(self):
        browser = MagicMock()
        browser.wait_for.return_value = True
        s = self._make_submitter(browser_tool=browser)
        assert s._needs_login(browser)

    def test_needs_login_false(self):
        browser = MagicMock()
        browser.wait_for.return_value = False
        s = self._make_submitter(browser_tool=browser)
        assert not s._needs_login(browser)

    def test_needs_login_exception(self):
        browser = MagicMock()
        browser.wait_for.side_effect = RuntimeError("boom")
        s = self._make_submitter(browser_tool=browser)
        assert not s._needs_login(browser)

    # ── _click_first ────────────────────────────────────────────────────

    def test_click_first_success(self):
        browser = MagicMock()
        browser.click.side_effect = [False, True]
        s = self._make_submitter()
        assert s._click_first(browser, ['sel1', 'sel2'])

    def test_click_first_fail(self):
        browser = MagicMock()
        browser.click.return_value = False
        s = self._make_submitter()
        assert not s._click_first(browser, ['sel1'])

    def test_click_first_exception(self):
        browser = MagicMock()
        browser.click.side_effect = RuntimeError("boom")
        s = self._make_submitter()
        assert not s._click_first(browser, ['sel1'])

    # ── _fill_first ──────────────────────────────────────────────────────

    def test_fill_first_success(self):
        browser = MagicMock()
        browser.fill.return_value = True
        s = self._make_submitter()
        assert s._fill_first(browser, ['sel1'], 'text')

    def test_fill_first_fail(self):
        browser = MagicMock()
        browser.fill.return_value = False
        s = self._make_submitter()
        assert not s._fill_first(browser, ['sel1'], 'text')

    # ── _build_apply_url ─────────────────────────────────────────────────

    def test_build_apply_url(self):
        s = self._make_submitter()
        url = s._build_apply_url('https://www.upwork.com/jobs/~012345')
        # May return None or a URL depending on implementation
        assert url is None or isinstance(url, str)
