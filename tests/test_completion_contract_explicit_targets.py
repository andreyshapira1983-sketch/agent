# Run: "C:\Users\andre\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/test_completion_contract_explicit_targets.py

from core.completion_contract import derive_completion_contract


def test_explicit_change_declaration_defines_change_set_before_mixed_path_ambiguity():
    questions = (
        (
            "Прочитай core/loop.py для контекста. "
            "\nМЕНЯТЬ: ровно один файл - core/completion_contract.py; "
            "все остальные упомянутые пути только читать."
        ),
        (
            "Read core/loop.py for context. "
            "\nCHANGE: exactly one file - core/completion_contract.py; "
            "all other mentioned paths are read-only."
        ),
    )

    for question in questions:
        contract = derive_completion_contract(question)

        assert not contract.ambiguities, contract.ambiguities
        assert {
            obligation.target
            for obligation in contract.obligations
            if obligation.deliverable == "file_modified"
        } == {"core/completion_contract.py"}
