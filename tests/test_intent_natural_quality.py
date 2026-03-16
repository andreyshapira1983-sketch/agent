from src.core.intent import interpret_intent


def test_intent_quality_status_phrase() -> None:
    out = interpret_intent("покажи качество и историю")
    assert out["command"] == "get_quality"


def test_intent_quality_export_full_phrase() -> None:
    out = interpret_intent("выгрузи полный отчет качества")
    assert out["command"] == "export_quality_full"


def test_intent_quality_export_json_phrase() -> None:
    out = interpret_intent("экспорт quality в json")
    assert out["command"] == "export_quality_json"


def test_intent_reset_quality_phrase() -> None:
    out = interpret_intent("сбрось метрики качества")
    assert out["command"] == "reset_quality"


def test_intent_regular_chat_phrase() -> None:
    out = interpret_intent("просто поболтаем")
    assert out["command"] == "chat"
