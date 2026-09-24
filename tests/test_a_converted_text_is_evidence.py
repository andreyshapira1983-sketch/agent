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


def test_the_evidence_is_found_by_the_label_the_answer_cites():
    """Приёмка урока 1, 24.09: улика была «file:scan.png», а ответ ссылался на метку
    шага «convert_file:ocr:scan.png» — 9 из 11 ссылок остались без улики."""
    from tools.convert_file import sanitize_args

    args = {"op": "ocr", "path": "inbox/lesson_01/scan.png"}
    step = sanitize_args(dict(args), 0, [])
    ev = evidence_from_tool_result(tool_name="convert_file", arguments=step["arguments"],
                                   output={"exit_code": 0, "text": "ИТОГО 47250"})
    assert ev.source_id == step["label"]


def test_a_file_citation_of_the_source_finds_the_converted_text():
    """24.09, живой прогон приёмки: с меткой «convert_file:…» (такого вида ссылки
    проверщик не знает) писатель ответа цитировал листинг папки, и верные числа
    получили «опровергнуто». Ссылка [file:<исходный файл>] должна находить улику."""
    from core.evidence import ProvenanceChain
    from core.verifier_core import verify
    from tools.convert_file import sanitize_args

    step = sanitize_args({"op": "ocr", "path": "inbox/lesson_01/scan.png"}, 0, [])
    ev = evidence_from_tool_result(tool_name="convert_file", arguments=step["arguments"],
                                   output={"exit_code": 0, "text": "ИТОГО: 47 250 руб."})
    assert ev.source_id == step["label"]
    chain = ProvenanceChain()
    chain.add(ev)
    report = verify(answer="На скане итог 47 250 руб. [file:inbox/lesson_01/scan.png]",
                    chain=chain, llm=None, expects_contract_headers=False)
    assert report.chunks[0].verdict == "verified"
