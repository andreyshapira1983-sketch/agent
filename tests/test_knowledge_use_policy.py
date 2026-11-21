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
