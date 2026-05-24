"""Tests for channels/email_intake.py — IMAP intake channel.

Strategy: never touch the network. We inject a fake IMAP client that
returns hand-rolled RFC822 messages and assert the channel produces the
right `PolledEmail`s and Jobs.
"""

from __future__ import annotations

import email
import email.message
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from brain.secrets import Secret, SecretsVault
from brain.skills.job import Job, JobStatus, JobStore
from channels.email_intake import (
    EmailIntakeChannel,
    EmailIntakeError,
    PolledEmail,
)


# ════════════════════════════════════════════════════════════════════
# Fakes
# ════════════════════════════════════════════════════════════════════

@dataclass
class FakeIMAPClient:
    """Bare-minimum IMAPClientProtocol stub for unit tests."""

    messages: dict[str, bytes]          # uid -> raw RFC822 bytes
    failed_uids: set[str] = field(default_factory=set)
    logged_in: bool = False
    selected: str = ""
    seen_flagged: set[str] = field(default_factory=set)
    logged_out: bool = False
    auth_error: bool = False

    def login(self, user, password):
        if self.auth_error:
            raise RuntimeError("bad credentials")
        self.logged_in = True
        return ("OK", [b"Logged in"])

    def select(self, mailbox="INBOX", readonly=False):
        self.selected = mailbox
        return ("OK", [str(len(self.messages)).encode()])

    def search(self, charset, *criteria):
        uids = " ".join(self.messages.keys())
        return ("OK", [uids.encode("ascii")])

    def fetch(self, uid, parts):
        if uid in self.failed_uids:
            return ("NO", [b""])
        raw = self.messages.get(str(uid))
        if raw is None:
            return ("NO", [b""])
        # imaplib payload shape
        return ("OK", [(f"{uid} (RFC822 {{{len(raw)}}}".encode(), raw), b")"])

    def store(self, uid, command, flags):
        self.seen_flagged.add(str(uid))
        return ("OK", [b"flag set"])

    def logout(self):
        self.logged_out = True
        return ("BYE", [b"logged out"])


def _make_vault(**overrides) -> SecretsVault:
    vault = SecretsVault()
    base = {
        "IMAP_USERNAME": "agent@example.com",
        "IMAP_PASSWORD": "topsecret",
        "IMAP_HOST":     "imap.example.com",
        "IMAP_PORT":     "993",
    }
    base.update(overrides)
    for k, v in base.items():
        vault.set(k, v)
    return vault


def _make_rfc822(
    *,
    subject: str = "test",
    body: str = "hello world",
    sender: str = "client@example.com",
    message_id: str = "<id-1@example.com>",
    attachments: list[tuple[str, bytes]] | None = None,
) -> bytes:
    """Build a multipart RFC822 message in bytes."""
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["To"] = "agent@example.com"
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    msg.set_content(body)
    if attachments:
        for fname, data in attachments:
            msg.add_attachment(
                data,
                maintype="application",
                subtype="octet-stream",
                filename=fname,
            )
    return msg.as_bytes()


def _factory(client: FakeIMAPClient):
    def _f(_host: str, _port: int):
        return client
    return _f


# ════════════════════════════════════════════════════════════════════
# Poll: happy path
# ════════════════════════════════════════════════════════════════════

class TestPoll:

    def test_poll_simple_message(self, tmp_path):
        raw = _make_rfc822(subject="Edit my doc", body="Please edit")
        fake = FakeIMAPClient(messages={"1": raw})
        vault = _make_vault()

        channel = EmailIntakeChannel(
            vault=vault,
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        polled = channel.poll()
        assert len(polled) == 1
        msg = polled[0]
        assert isinstance(msg, PolledEmail)
        assert msg.subject == "Edit my doc"
        assert msg.from_addr == "client@example.com"
        assert msg.message_id == "<id-1@example.com>"
        assert "Please edit" in msg.body
        assert msg.attachments == []
        # mark_seen=True by default
        assert "1" in fake.seen_flagged
        assert fake.logged_out

    def test_poll_with_attachment(self, tmp_path):
        raw = _make_rfc822(
            subject="See attached",
            body="See file",
            attachments=[("report.docx", b"XYZ" * 100)],
        )
        fake = FakeIMAPClient(messages={"42": raw})
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        polled = channel.poll()
        assert len(polled) == 1
        msg = polled[0]
        assert len(msg.attachments) == 1
        path = msg.attachments[0]
        assert path.exists()
        assert path.read_bytes() == b"XYZ" * 100
        # Filename was preserved (safe form)
        assert "report" in path.name

    def test_poll_empty_inbox(self, tmp_path):
        fake = FakeIMAPClient(messages={})
        # search returns empty UID list
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        assert channel.poll() == []

    def test_oversize_attachment_dropped(self, tmp_path):
        raw = _make_rfc822(
            attachments=[("huge.bin", b"0" * 1000)],
        )
        fake = FakeIMAPClient(messages={"1": raw})
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
            max_attachment_bytes=500,
        )
        polled = channel.poll()
        assert len(polled) == 1
        assert polled[0].attachments == []

    def test_unsafe_filename_sanitised(self, tmp_path):
        raw = _make_rfc822(
            attachments=[("../../../etc/passwd", b"data")],
        )
        fake = FakeIMAPClient(messages={"1": raw})
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        polled = channel.poll()
        path = polled[0].attachments[0]
        # No path traversal — must stay under attachments_dir
        assert str(tmp_path) in str(path.resolve())
        assert ".." not in path.name


# ════════════════════════════════════════════════════════════════════
# Errors
# ════════════════════════════════════════════════════════════════════

class TestErrors:

    def test_missing_credentials_raises(self, tmp_path):
        vault = SecretsVault()  # empty
        channel = EmailIntakeChannel(
            vault=vault,
            attachments_dir=tmp_path,
            imap_factory=_factory(FakeIMAPClient(messages={})),
        )
        with pytest.raises(EmailIntakeError):
            channel.poll()

    def test_connect_failure_raises(self, tmp_path):
        def boom(_h, _p):
            raise OSError("connect refused")
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=boom,
        )
        with pytest.raises(EmailIntakeError):
            channel.poll()

    def test_fetch_failure_skips_uid(self, tmp_path):
        raw_good = _make_rfc822(subject="good")
        fake = FakeIMAPClient(
            messages={"1": raw_good, "2": b"unused"},
            failed_uids={"2"},
        )
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        polled = channel.poll()
        assert len(polled) == 1
        assert polled[0].subject == "good"


# ════════════════════════════════════════════════════════════════════
# to_job
# ════════════════════════════════════════════════════════════════════

class TestToJob:

    def test_to_job_uses_subject_as_brief(self, tmp_path):
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(FakeIMAPClient(messages={})),
        )
        polled = PolledEmail(
            uid="1",
            message_id="<a@b>",
            from_addr="client@example.com",
            subject="Edit my doc please",
            body="full body",
            attachments=[Path("/tmp/x.docx")],
        )
        job = channel.to_job(polled)
        assert isinstance(job, Job)
        assert job.brief == "Edit my doc please"
        assert job.source == "email"
        assert job.client_id == "client@example.com"
        # Path roundtripped to str — exact form is OS-dependent
        assert len(job.attachments) == 1
        assert "x.docx" in job.attachments[0]
        assert job.status is JobStatus.RECEIVED

    def test_to_job_falls_back_to_body_when_subject_blank(self, tmp_path):
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(FakeIMAPClient(messages={})),
        )
        polled = PolledEmail(
            uid="1", message_id="x", from_addr="c@x",
            subject="   ", body="please edit this",
        )
        job = channel.to_job(polled)
        assert job.brief == "please edit this"


# ════════════════════════════════════════════════════════════════════
# poll_and_dispatch
# ════════════════════════════════════════════════════════════════════

class _FakeBrain:
    def __init__(self):
        self.calls = []
    def intake_job(self, job: Job):
        self.calls.append(job)
        from brain.skills.workflow_runner import JobOutcome
        return JobOutcome(
            job_id=job.id, profession_id="text_editor",
            final_status=JobStatus.DELIVERED,
        )


class TestPollAndDispatch:

    def test_creates_jobs_and_calls_brain(self, tmp_path):
        raw1 = _make_rfc822(subject="edit this", message_id="<a@b>")
        raw2 = _make_rfc822(subject="another", message_id="<c@d>")
        fake = FakeIMAPClient(messages={"1": raw1, "2": raw2})
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        brain = _FakeBrain()
        store = JobStore(tmp_path / "jobs.db")
        try:
            results = channel.poll_and_dispatch(brain, store)
        finally:
            store.close()

        assert len(results) == 2
        assert len(brain.calls) == 2
        assert all(j.source == "email" for j in brain.calls)

    def test_on_outcome_hook_called(self, tmp_path):
        raw = _make_rfc822(subject="edit")
        fake = FakeIMAPClient(messages={"1": raw})
        channel = EmailIntakeChannel(
            vault=_make_vault(),
            attachments_dir=tmp_path,
            imap_factory=_factory(fake),
        )
        brain = _FakeBrain()
        store = JobStore(tmp_path / "jobs.db")
        captured = []

        def hook(mail, outcome):
            captured.append((mail.subject, outcome.final_status))

        try:
            channel.poll_and_dispatch(brain, store, on_outcome=hook)
        finally:
            store.close()

        assert captured == [("edit", JobStatus.DELIVERED)]
