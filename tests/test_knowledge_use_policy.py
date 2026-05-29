from core.knowledge_use_policy import KnowledgeUsePolicy
from core.models import MemoryRecord
from core.role_router import RoleRouter


def _rec(content: str, tags: list[str]) -> MemoryRecord:
    return MemoryRecord(type="semantic", content=content, tags=tags, owner="self")


def test_allows_records_matching_current_role_tags():
    ctx = RoleRouter().route("почини failing tests")
    records = [
        _rec("Self-repair uses run_tests before applying patches.", ["repair", "tests"]),
        _rec("User prefers short answers.", ["preference"]),
    ]
    report = KnowledgeUsePolicy().filter(records, role_context=ctx, question="failing tests")
    # Preferences are allowed as candidates because retrieval still applies
    # keyword scoring afterwards; the repair lesson is allowed by role tags.
    assert records[0] in report.allowed
    assert records[1] in report.allowed
    assert report.rejected_count == 0


def test_blocks_quarantined_records_even_when_keywords_match():
    ctx = RoleRouter().route("python memory policy")
    record = _rec("Python memory policy lesson", ["knowledge", "quarantine"])
    report = KnowledgeUsePolicy().filter([record], role_context=ctx, question="python memory policy")
    assert report.allowed == []
    assert report.decisions[0].decision == "reject"
    assert "blocked tag" in report.decisions[0].reasons[0]


def test_allows_question_overlap_even_without_role_tag():
    ctx = RoleRouter().route("объясни архитектуру memory")
    record = _rec("Memory records are injected only when relevant.", ["source-backed"])
    report = KnowledgeUsePolicy().filter([record], role_context=ctx, question="memory relevant")
    assert report.allowed == [record]
