from core.role_router import RoleRouter


def test_routes_repair_requests_to_repair_role():
    ctx = RoleRouter().route("Почини self-repair ошибку и покажи diff")
    assert ctx.role == "repair"
    assert ctx.output_style == "plan"
    assert "self_repair" in ctx.knowledge_scopes
    assert "repair" in ctx.allowed_memory_tags


def test_routes_learning_requests_to_learning_role():
    ctx = RoleRouter().route("изучи проект и запомни полезные знания")
    assert ctx.role == "learning"
    assert ctx.tone == "technical"
    assert "learning" in ctx.knowledge_scopes


def test_routes_operator_chat_by_default():
    ctx = RoleRouter().route("Что дальше делать?")
    assert ctx.role == "operator_chat"
    assert ctx.tone == "human"
    assert ctx.output_style == "conversation"
