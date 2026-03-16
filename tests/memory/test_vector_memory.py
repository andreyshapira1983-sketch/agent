from src.memory.vector_memory import VectorMemory


def test_vector_memory():
    vm = VectorMemory()

    vm.add("first text", {"source": "source1", "type": "text"})
    vm.store("second item")
    assert len(vm) == 2

    results = vm.search("first", k=5)
    assert len(results) == 1
    assert results[0]["text"] == "first text"
    assert results[0]["meta"]["source"] == "source1"

    results = vm.retrieve("item")
    assert len(results) == 1
    assert "second" in results[0]["text"]

    vm.clear()
    assert len(vm) == 0
