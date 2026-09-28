from pathlib import Path

from core.models import ToolCall
from core.tool_receipts import ToolReceiptLedger, default_receipts_path, receipt_context
from tools.convert_file import ConvertFileTool


def test_a_converted_file_leaves_a_receipt(tmp_path: Path) -> None:
    """Вызов convert_file оставляет строку в журнале квитанций.

    Без квитанции вывод конвертации нечем подтвердить как наблюдение инструмента.
    """
    (tmp_path / "a.docx").write_bytes(b"x")

    def fake(argv: list[str], cwd: Path, timeout: int) -> tuple[int, str]:
        (cwd / "out" / "in.txt").write_text("ИТОГО 47250", encoding="utf-8")
        return 0, ""

    tool = ConvertFileTool(workspace_root=tmp_path, runner=fake)
    call = ToolCall(action_id="a1", tool_name="convert_file",
                    arguments={"op": "office", "path": "a.docx", "to": "txt"})
    with receipt_context(trace_id="tr_convert", path="repl", workspace=tmp_path):
        result = tool.invoke(call)

    assert result.status == "success", result.error
    receipts = ToolReceiptLedger(default_receipts_path(tmp_path)).load()
    assert [r.operation for r in receipts] == ["convert_file"]
    assert receipts[0].trace_id == "tr_convert"
    assert receipts[0].refs["tool_call_id"] == call.id
