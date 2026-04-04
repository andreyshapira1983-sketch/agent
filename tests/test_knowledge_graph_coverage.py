"""Тесты для knowledge/knowledge_graph.py — KnowledgeGraph (in-memory graph of triples)."""

import pytest
from knowledge.knowledge_graph import KnowledgeGraph


class TestKnowledgeGraphBasic:
    def test_empty_graph(self):
        g = KnowledgeGraph()
        assert len(g) == 0
        assert g.concepts() == []
        assert g.predicates() == []
        assert g.all_triples() == []

    def test_add_triple(self):
        g = KnowledgeGraph()
        g.add_triple("Python", "is_a", "language")
        assert len(g) == 1
        assert "Python" in g.concepts()
        assert "language" in g.concepts()
        assert "is_a" in g.predicates()

    def test_add_duplicate_ignored(self):
        g = KnowledgeGraph()
        g.add_triple("A", "rel", "B")
        g.add_triple("A", "rel", "B")
        assert len(g) == 1

    def test_add_empty_parts_ignored(self):
        g = KnowledgeGraph()
        g.add_triple("", "rel", "B")
        g.add_triple("A", "", "B")
        g.add_triple("A", "rel", "")
        assert len(g) == 0

    def test_add_strips_whitespace(self):
        g = KnowledgeGraph()
        g.add_triple("  A  ", " rel ", " B ")
        triples = g.all_triples()
        assert triples == [("A", "rel", "B")]


class TestKnowledgeGraphRemove:
    def test_remove_triple(self):
        g = KnowledgeGraph()
        g.add_triple("A", "rel", "B")
        g.remove_triple("A", "rel", "B")
        assert len(g) == 0

    def test_remove_nonexistent(self):
        g = KnowledgeGraph()
        g.remove_triple("X", "Y", "Z")  # no error
        assert len(g) == 0

    def test_remove_concept(self):
        g = KnowledgeGraph()
        g.add_triple("A", "r1", "B")
        g.add_triple("A", "r2", "C")
        g.add_triple("D", "r3", "A")
        g.remove_concept("A")
        assert len(g) == 0
        assert "A" not in g.concepts()


class TestKnowledgeGraphQueries:
    @pytest.fixture
    def graph(self):
        g = KnowledgeGraph()
        g.add_triple("Python", "is_a", "language")
        g.add_triple("Python", "used_for", "data_science")
        g.add_triple("pandas", "part_of", "Python")
        g.add_triple("numpy", "part_of", "Python")
        return g

    def test_get_related(self, graph):
        related = graph.get_related("Python")
        assert len(related) == 2
        preds = {r["predicate"] for r in related}
        assert "is_a" in preds
        assert "used_for" in preds

    def test_get_related_with_filter(self, graph):
        related = graph.get_related("Python", predicate="is_a")
        assert len(related) == 1
        assert related[0]["object"] == "language"

    def test_get_related_empty(self, graph):
        assert graph.get_related("nonexistent") == []

    def test_get_incoming(self, graph):
        incoming = graph.get_incoming("Python")
        assert len(incoming) == 2
        subjects = {r["subject"] for r in incoming}
        assert "pandas" in subjects
        assert "numpy" in subjects

    def test_get_incoming_with_filter(self, graph):
        incoming = graph.get_incoming("Python", predicate="part_of")
        assert len(incoming) == 2

    def test_get_incoming_none(self, graph):
        assert graph.get_incoming("nonexistent_concept") == []

    def test_query_object_wildcard(self, graph):
        # Python is_a ?
        results = graph.query("Python is_a ?")
        assert len(results) == 1
        assert results[0]["object"] == "language"

    def test_query_subject_wildcard(self, graph):
        # ? part_of Python
        results = graph.query("? part_of Python")
        assert len(results) == 2

    def test_query_predicate_wildcard(self, graph):
        # Python ? data_science
        results = graph.query("Python ? data_science")
        assert len(results) == 1
        assert results[0]["predicate"] == "used_for"

    def test_query_all_wildcards(self, graph):
        results = graph.query("Python ? ?")
        assert len(results) == 2

    def test_query_bad_format(self, graph):
        assert graph.query("too few") == []
        assert graph.query("") == []

    def test_query_no_match(self, graph):
        assert graph.query("Java is_a ?") == []


class TestKnowledgeGraphTraversal:
    @pytest.fixture
    def chain(self):
        """A -> B -> C -> D"""
        g = KnowledgeGraph()
        g.add_triple("A", "to", "B")
        g.add_triple("B", "to", "C")
        g.add_triple("C", "to", "D")
        return g

    def test_traverse_depth_0(self, chain):
        result = chain.traverse("A", max_depth=0)
        assert result == {"A": 0}

    def test_traverse_depth_1(self, chain):
        result = chain.traverse("A", max_depth=1)
        assert result == {"A": 0, "B": 1}

    def test_traverse_depth_2(self, chain):
        result = chain.traverse("A", max_depth=2)
        assert "C" in result
        assert result["C"] == 2

    def test_traverse_with_predicate_filter(self, chain):
        chain.add_triple("A", "other", "X")
        result = chain.traverse("A", max_depth=2, predicate="to")
        assert "X" not in result
        assert "B" in result

    def test_find_path_exists(self, chain):
        path = chain.find_path("A", "D")
        assert path is not None
        assert len(path) == 3
        assert path[0]["subject"] == "A"
        assert path[-1]["object"] == "D"

    def test_find_path_same_node(self, chain):
        path = chain.find_path("A", "A")
        assert path == []

    def test_find_path_no_path(self, chain):
        path = chain.find_path("D", "A")  # no reverse edges
        assert path is None

    def test_find_path_max_depth(self, chain):
        path = chain.find_path("A", "D", max_depth=1)
        assert path is None

    def test_get_neighborhood(self, chain):
        nb = chain.get_neighborhood("B", depth=1)
        assert "B" in nb["concepts"]
        assert "C" in nb["concepts"]
        assert len(nb["triples"]) >= 1


class TestKnowledgeGraphBulkAndMerge:
    def test_add_triples_bulk(self):
        g = KnowledgeGraph()
        g.add_triples_bulk([
            ("A", "r", "B"),
            ("C", "r", "D"),
            ("A", "r", "B"),  # duplicate
        ])
        assert len(g) == 2

    def test_merge(self):
        g1 = KnowledgeGraph()
        g1.add_triple("A", "r", "B")
        g2 = KnowledgeGraph()
        g2.add_triple("C", "r", "D")
        g2.add_triple("A", "r", "B")  # duplicate across graphs
        g1.merge(g2)
        assert len(g1) == 2


class TestKnowledgeGraphStats:
    def test_summary(self):
        g = KnowledgeGraph()
        g.add_triple("A", "r1", "B")
        g.add_triple("A", "r2", "C")
        s = g.summary()
        assert s["concepts"] == 3
        assert s["triples"] == 2
        assert s["predicates"] == 2
        assert len(s["top_concepts"]) <= 5

    def test_repr(self):
        g = KnowledgeGraph()
        g.add_triple("A", "r", "B")
        r = repr(g)
        assert "2 concepts" in r
        assert "1 triples" in r
