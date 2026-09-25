"""Agent Market: полный цикл исполнителя против поддельной площадки по её контракту.

Поддельная площадка — настоящий HTTP-сервер на 127.0.0.1 с путями и формами
ответов из /openapi.json и worker-lifecycle.md: клиент идёт по тому же
urllib/http.client, что и к market.near.ai. Живой площадки тест не трогает.
"""
from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from core.market_client import MarketClient, MarketError
from core.market_worker import MarketWorker, classify

TOKEN = "aat_" + "0123456789abcdef" * 2


class _Market:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_first: dict[str, list[int]] = {}
        self.files: dict[str, dict] = {}
        self.posted: dict[str, list[str]] = {}          # assignmentId -> тексты агента в переписке
        self.job_files: dict[str, list[dict]] = {}      # jobId -> вложения работы
        self.blobs: dict[str, bytes] = {}               # /dl/<id> -> байты вложения
        self.thread: dict[str, list[dict]] = {}         # assignmentId -> переписка, старые первыми
        self.assignments = {
            "as-1": {"assignment": {"assignmentId": "as-1", "jobId": "job-test", "status": "in_progress",
                                    "startedAt": None, "submittedAt": None, "deliverableUrl": None},
                     "job": {"jobId": "job-test", "title": "Посчитай 2+2", "description": "Ответ числом.",
                             "tags": ["math"]}, "latestMessage": None},
            "as-2": {"assignment": {"assignmentId": "as-2", "jobId": "job-foreign", "status": "in_progress",
                                    "startedAt": None, "submittedAt": None, "deliverableUrl": None},
                     "job": {"jobId": "job-foreign", "title": "Чужая работа"}, "latestMessage": None},
        }


def _serve(market: _Market):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a) -> None:  # тише в выводе тестов
            pass

        def _reply(self, code: int, body: dict | None = None, headers: dict | None = None) -> None:
            data = json.dumps(body or {}).encode()
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str) -> None:  # noqa: PLR0911 — одна ветка на путь поддельной площадки
            path = self.path.split("?", 1)[0]
            market.calls.append((method, self.path))
            queue = market.fail_first.get(path)
            if queue:
                code = queue.pop(0)
                return self._reply(code, {"error": "boom"}, {"Retry-After": "0"} if code == 429 else None)
            if path.startswith("/upload/"):
                n = int(self.headers.get("Content-Length", 0))
                market.files[path.rsplit("/", 1)[1]]["bytes"] = self.rfile.read(n)
                market.files[path.rsplit("/", 1)[1]]["put_headers"] = dict(self.headers)
                return self._reply(200)
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                return self._reply(401, {"error": "unauthorized"})
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if path.startswith("/dl/"):
                blob = market.blobs[path[4:]]
                self.send_response(200)
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return None
            m = re.fullmatch(r"/v1/jobs/([\w-]+)/attachments", path)
            if m:
                return self._reply(200, {"attachments": market.job_files.get(m.group(1), [])})
            m = re.fullmatch(r"/v1/assignments/([\w-]+)/messages", path)
            if m and method == "GET":  # flows/messaging.md: переписка, новые сверху
                return self._reply(200, {"messages": list(reversed(market.thread.get(m.group(1), [])))})
            if m and method == "POST":
                assert 1 <= len(body["body"]) <= 4000, "площадка отвергает пустое и длиннее 4000"
                market.posted.setdefault(m.group(1), []).append(body["body"])
                market.thread.setdefault(m.group(1), []).append({"senderAgentId": "me", "body": body["body"]})
                row = market.assignments[m.group(1)]
                row["latestMessage"] = {"senderSide": "worker", "origin": "direct", "body": body["body"],
                                        "truncated": False, "attachments": [], "createdAt": "2026-09-25T11:00:00Z"}
                return self._reply(201, {"messageId": "m1"})
            if path == "/v1/agents/me/assignments":
                status = re.search(r"status=(\w+)", self.path).group(1)
                rows = [r for r in market.assignments.values()
                        if status == "all" or r["assignment"]["status"] == status]
                for r in rows:  # слово покупателя площадка хранит в переписке
                    msg, aid = r.get("latestMessage") or {}, r["assignment"]["assignmentId"]
                    if msg.get("senderSide") == "buyer" and msg not in market.thread.get(aid, []):
                        market.thread.setdefault(aid, []).append(dict(msg))
                return self._reply(200, {"assignments": rows})
            m = re.fullmatch(r"/v1/assignments/([\w-]+)/(start|submit)", path)
            if m:
                a = market.assignments[m.group(1)]["assignment"]
                if m.group(2) == "start":
                    a["startedAt"] = "2026-09-25T10:00:00Z"
                    return self._reply(200, {"startedAt": a["startedAt"]})
                if not body.get("deliverableUrl", "").startswith(("https://", "http://127.0.0.1")):
                    return self._reply(400, {"error": "validation_error", "message": "bad url"})
                a.update(status="submitted", submittedAt="2026-09-25T10:05:00Z",
                         deliverableUrl=body["deliverableUrl"], deliverableHash=body.get("deliverableHash"))
                return self._reply(200, a)
            if path == "/v1/files":
                fid = f"f{len(market.files) + 1}"
                base = f"http://127.0.0.1:{self.server.server_address[1]}"
                market.files[fid] = {"meta": body, "completed": False}
                return self._reply(201, {"fileId": fid, "uploadUrl": f"{base}/files/{fid}",
                                         "directUploadUrl": f"{base}/upload/{fid}?sig=secret",
                                         "fileUrl": f"{base}/files/{fid}/{body['filename']}",
                                         "byteSize": body["byteSize"], "expiresInSecs": 1800})
            m = re.fullmatch(r"/v1/files/(\w+)/complete", path)
            if m:
                market.files[m.group(1)]["completed"] = True
                return self._reply(200, {"fileId": m.group(1)})
            return self._reply(404, {"error": "not_found"})

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_PUT(self) -> None:
            self._handle("PUT")

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def market():
    m = _Market()
    server = _serve(m)
    m.base = f"http://127.0.0.1:{server.server_address[1]}"
    yield m
    server.shutdown()


def _client(market, tmp_path: Path, **kw) -> MarketClient:
    return MarketClient(token=TOKEN, base_url=market.base, log_path=tmp_path / "market_api.jsonl",
                        sleep=lambda s: None, **kw)


def test_the_full_cycle_assignment_agent_submit_status(market, tmp_path: Path) -> None:
    asked: list[str] = []
    worker = MarketWorker(_client(market, tmp_path), lambda text: asked.append(text) or "# Ответ\n\n4",
                          allowed_jobs={"job-test"}, workdir=tmp_path / "market")
    reports = {r.assignment_id: r for r in worker.poll_once()}
    done = reports["as-1"]
    assert done.error is None, done
    assert done.steps[0] == "start" and "submit" in done.steps and done.status_after == "submitted"
    assert "Посчитай 2+2" in asked[0]
    f = next(iter(market.files.values()))
    assert f["completed"] and f["bytes"] == "# Ответ\n\n4".encode()
    assert "Authorization" not in f["put_headers"] and "Content-Type" not in f["put_headers"]
    a = market.assignments["as-1"]["assignment"]
    assert a["status"] == "submitted" and len(a["deliverableHash"]) == 64


def test_a_job_not_allowed_by_the_operator_is_not_even_started(market, tmp_path: Path) -> None:
    worker = MarketWorker(_client(market, tmp_path), lambda text: "x", allowed_jobs=set(),
                          workdir=tmp_path / "market")
    reports = worker.poll_once()
    assert all("не взято" in r.steps[0] for r in reports)
    assert not [c for c in market.calls if c[0] == "POST"], "nothing may be started or submitted"
    assert market.assignments["as-1"]["assignment"]["startedAt"] is None


def test_no_bid_is_ever_placed(market, tmp_path: Path) -> None:
    worker = MarketWorker(_client(market, tmp_path), lambda text: "ok", allowed_jobs={"job-test"},
                          workdir=tmp_path / "market")
    worker.poll_once()
    assert not [c for c in market.calls if "/bids" in c[1] and c[0] == "POST"]
    assert not hasattr(MarketClient, "place_bid")


def test_server_errors_and_rate_limits_are_retried(market, tmp_path: Path) -> None:
    market.fail_first["/v1/agents/me/assignments"] = [503, 429]
    rows = _client(market, tmp_path).my_assignments("all")
    assert len(rows) == 2
    log = [json.loads(x) for x in (tmp_path / "market_api.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["status"] for r in log] == [503, 429, 200]


def test_a_client_error_is_not_retried(market, tmp_path: Path) -> None:
    client = MarketClient(token="aat_" + "f" * 32, base_url=market.base, log_path=tmp_path / "m.jsonl",
                          sleep=lambda s: None)
    with pytest.raises(MarketError) as err:
        client.my_assignments()
    assert err.value.status == 401 and err.value.code == "unauthorized"
    assert len((tmp_path / "m.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_the_token_never_reaches_the_log(market, tmp_path: Path) -> None:
    worker = MarketWorker(_client(market, tmp_path), lambda text: f"echo {TOKEN}", allowed_jobs={"job-test"},
                          workdir=tmp_path / "market")
    worker.poll_once()
    text = (tmp_path / "market_api.jsonl").read_text(encoding="utf-8")
    assert TOKEN not in text and "sig=secret" not in text


def test_plain_http_is_refused_outside_localhost() -> None:
    with pytest.raises(MarketError):
        MarketClient(token=TOKEN, base_url="http://market.near.ai")


@pytest.mark.parametrize(("row", "kind"), [
    ({"status": "in_progress", "startedAt": None, "deliverableUrl": None}, "fresh"),
    ({"status": "in_progress", "startedAt": "t", "deliverableUrl": None}, "in_flight"),
    ({"status": "in_progress", "startedAt": "t", "deliverableUrl": "u", "submittedAt": "t"}, "rework"),
    ({"status": "in_progress", "startedAt": "t", "deliverableUrl": "u", "submittedAt": None}, "redo"),
    ({"status": "accepted"}, "accepted"),
])
def test_rows_are_classified_as_the_lifecycle_table_says(row: dict, kind: str) -> None:
    assert classify({"assignment": row}) == kind


# ── пункты 2–4 (2026-09-25): переписка, вложения, срок ──────────────────────
def _only_allowed(market) -> None:
    del market.assignments["as-2"]


def _msg(body: str, created: str, attachments: list | None = None, truncated: bool = False) -> dict:
    return {"senderSide": "buyer", "origin": "direct", "body": body, "truncated": truncated,
            "attachments": attachments or [], "createdAt": created}


def _att(market, name: str, data: bytes, sha: str | None = None) -> dict:
    import hashlib
    market.blobs[name] = data
    return {"id": name, "filename": name, "contentType": "text/plain", "byteSize": len(data),
            "sha256": sha or hashlib.sha256(data).hexdigest(), "downloadUrl": f"{market.base}/dl/{name}",
            "createdAt": "2026-09-25T09:00:00Z"}


def _worker(market, tmp_path: Path, asked: list[str], reply: str = "Итог: 4") -> MarketWorker:
    return MarketWorker(_client(market, tmp_path), lambda text: asked.append(text) or reply,
                        allowed_jobs={"job-test"}, workdir=tmp_path / "market")


def test_buyer_files_are_downloaded_verified_and_shown_to_the_agent(market, tmp_path: Path) -> None:
    _only_allowed(market)
    market.job_files["job-test"] = [_att(market, "spec.txt", "числа: 2 и 2".encode())]
    market.assignments["as-1"]["latestMessage"] = _msg(
        "См. файл", "2026-09-25T09:00:00Z", [_att(market, "bad.txt", b"xx", sha="0" * 64)])
    asked: list[str] = []
    report = _worker(market, tmp_path, asked).poll_once()[0]
    assert report.error is None, report
    assert "числа: 2 и 2" in asked[0], "the agent must see the text of the buyer's file"
    assert "SHA-256 не сошёлся" in asked[0], "a file failing its hash is dropped, and said so"
    assert (tmp_path / "market" / "as-1" / "inbox" / "spec.txt").read_bytes() == "числа: 2 и 2".encode()


def test_the_token_is_never_sent_to_a_foreign_host(market, tmp_path: Path) -> None:
    with pytest.raises(MarketError) as err:
        _client(market, tmp_path).download("https://evil.example/steal")
    assert err.value.code == "foreign_host"


def test_a_question_about_submitted_work_is_answered_once(market, tmp_path: Path) -> None:
    _only_allowed(market)
    row = market.assignments["as-1"]
    row["assignment"].update(status="submitted", startedAt="t", submittedAt="t", deliverableUrl="https://x/y")
    row["latestMessage"] = _msg("А почему 4?", "2026-09-25T10:30:00Z")
    asked: list[str] = []
    worker = _worker(market, tmp_path, asked, reply="Потому что 2+2=4.")
    worker.poll_once()
    assert market.posted["as-1"] == ["Потому что 2+2=4."]
    assert "А почему 4?" in asked[0] and "не переделывая" in asked[0]
    row["latestMessage"] = _msg("А почему 4?", "2026-09-25T10:30:00Z")   # тот же вопрос в следующем опросе
    worker.poll_once()
    assert market.posted["as-1"] == ["Потому что 2+2=4."], "one buyer message is answered once"
    assert row["assignment"]["status"] == "submitted", "a question is not a request to resubmit"


def test_rework_is_acknowledged_and_resubmitted(market, tmp_path: Path) -> None:
    _only_allowed(market)
    row = market.assignments["as-1"]
    row["assignment"].update(startedAt="t", submittedAt="t", deliverableUrl="https://x/old")
    row["latestMessage"] = _msg("Нужно подробнее", "2026-09-25T10:40:00Z")
    asked: list[str] = []
    report = _worker(market, tmp_path, asked).poll_once()[0]
    assert report.kind == "rework" and "submit" in report.steps
    assert market.posted["as-1"][0].startswith("Получил замечания")
    assert "Нужно подробнее" in asked[0]
    assert market.posted["as-1"][-1].startswith("Сдал результат")


def test_a_long_message_is_split_not_cut(market, tmp_path: Path) -> None:
    _only_allowed(market)
    _client(market, tmp_path).post_message("as-1", "я" * 9000)
    assert [len(x) for x in market.posted["as-1"]] == [4000, 4000, 1000]


def test_the_nearest_deadline_goes_first_and_a_passed_one_is_flagged(market, tmp_path: Path) -> None:
    market.assignments["as-2"]["assignment"]["jobId"] = "job-test"
    market.assignments["as-1"]["assignment"]["slaDeadlineAt"] = "2099-01-01T00:00:00Z"
    market.assignments["as-2"]["assignment"]["slaDeadlineAt"] = "2020-01-01T00:00:00Z"
    reports = _worker(market, tmp_path, []).poll_once()
    assert [r.assignment_id for r in reports] == ["as-2", "as-1"]
    assert any("срок (SLA) уже вышел" in s for s in reports[0].steps)
    assert not any("SLA" in s for s in reports[1].steps)


def test_every_order_gets_a_profit_line_that_the_payout_completes(market, tmp_path: Path) -> None:
    """Учёт прибыли по заказу (core/market_ledger.py): оплата − 5 % − модель."""
    from core.market_ledger import MarketLedger, summary

    market.assignments["as-1"]["assignment"].update(escrowAmount="2.00", escrowToken="USDC")
    worker = MarketWorker(_client(market, tmp_path), lambda text: "# Ответ\n\n4", allowed_jobs={"job-test"},
                          workdir=tmp_path / "market", cost_since=lambda since: 0.03)
    worker.poll_once()
    ledger = MarketLedger(tmp_path / "market" / "ledger.json")
    line = ledger.lines()["as-1"]
    assert (line.runs, line.model_usd, line.escrow_amount, line.net()) == (1, 0.03, 2.0, None)
    assert "as-2" not in ledger.lines(), "an order we did not work is not ours to count"

    market.assignments["as-1"]["assignment"].update(status="accepted", finalizedAt="2026-09-25T12:00:00Z")
    worker.poll_once()
    line = ledger.lines()["as-1"]
    assert line.status == "accepted" and line.accepted_at == "2026-09-25T12:00:00Z"
    assert line.net() == 1.87

    ledger.mark("as-1", claude_intervention=True, claude_minutes=6, operator_minutes=4)
    stats = summary(ledger.lines())
    assert stats["accepted"] == 1 and stats["claude_intervention_share"] == 1.0 and stats["unmarked"] == 0
    assert stats["net_usd_per_human_hour"] == round(1.87 / (10 / 60), 2)
    with pytest.raises(ValueError, match="not a manual column"):
        ledger.mark("as-1", model_usd=0)
