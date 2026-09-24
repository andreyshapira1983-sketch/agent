from core.evidence import evidence_from_tool_result


def test_converted_text_is_evidence():
    ev = evidence_from_tool_result(
        tool_name="convert_file",
        arguments={"op": "ocr", "path": "scan.png"},
        output={"text": "ИТОГО 47250", "outputs": ["converted/scan__ocr_1.txt"]},
    )
    assert ev is not None
    assert ev.kind == "file"
    assert "scan.png" in ev.source_id
    assert ev.obtained_via == "convert_file"


def test_office_conversion_produces_file_evidence():
    ev = evidence_from_tool_result(
        tool_name="convert_file",
        arguments={"op": "office", "path": "a.docx"},
        output={"exit_code": 0, "outputs": ["converted/a__office_1.pdf"]},
    )
    assert ev is not None
    assert ev.kind == "file"
    assert "a.docx" in ev.source_id
