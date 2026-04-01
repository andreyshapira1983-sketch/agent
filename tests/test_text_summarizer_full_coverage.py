import types
import unittest
from unittest.mock import patch

import numpy as np

from dynamic_modules.text_summarizer import TextSummarizer


class _FakeSparse:
    def __init__(self, arr):
        self._arr = np.array(arr, dtype=float)

    def toarray(self):
        return self._arr


class _FakeVectorizer:
    def __init__(self, stop_words=None):
        self.stop_words = stop_words
        self.last_sentences = None

    def fit_transform(self, sentences):
        self.last_sentences = list(sentences)
        rows = [[float(i + 1), float(len(s))] for i, s in enumerate(sentences)]
        return _FakeSparse(rows)


class _FakeKMeans:
    def __init__(self, n_clusters):
        self.n_clusters = n_clusters
        self.cluster_centers_ = None

    def fit(self, sentence_vectors):
        dense = sentence_vectors.toarray()
        self.cluster_centers_ = dense[: self.n_clusters]


class TextSummarizerTests(unittest.TestCase):
    def test_require_sklearn_module_success(self):
        math_mod = TextSummarizer._require_sklearn_module("math")
        self.assertTrue(hasattr(math_mod, "sqrt"))

    def test_require_sklearn_module_import_error(self):
        with patch("dynamic_modules.text_summarizer.importlib.import_module", side_effect=ImportError("x")):
            with self.assertRaises(RuntimeError) as ctx:
                TextSummarizer._require_sklearn_module("missing.module")
        self.assertIn("scikit-learn", str(ctx.exception))

    def test_init_and_get_sentence_vectors(self):
        fake_text_mod = types.SimpleNamespace(TfidfVectorizer=_FakeVectorizer)
        with patch.object(TextSummarizer, "_require_sklearn_module", return_value=fake_text_mod):
            ts = TextSummarizer()

        vectors, sentences = ts._get_sentence_vectors("one. two. three")
        self.assertEqual(sentences, ["one", "two", "three"])
        self.assertEqual(vectors.toarray().shape[0], 3)
        self.assertEqual(ts.vectorizer.stop_words, "english")

    def test_cluster_sentences(self):
        fake_text_mod = types.SimpleNamespace(TfidfVectorizer=_FakeVectorizer)
        fake_cluster_mod = types.SimpleNamespace(KMeans=_FakeKMeans)

        with patch.object(TextSummarizer, "_require_sklearn_module", side_effect=[fake_text_mod, fake_cluster_mod]):
            ts = TextSummarizer()
            sentence_vectors = _FakeSparse([[1, 1], [2, 2], [10, 10]])
            indices = ts._cluster_sentences(sentence_vectors, 2)

        self.assertEqual(indices, [0, 1])

    def test_summarize_with_min_target_length_and_output_format(self):
        fake_text_mod = types.SimpleNamespace(TfidfVectorizer=_FakeVectorizer)
        fake_cluster_mod = types.SimpleNamespace(KMeans=_FakeKMeans)

        with patch.object(TextSummarizer, "_require_sklearn_module", side_effect=[fake_text_mod, fake_cluster_mod]):
            ts = TextSummarizer()
            text = "alpha. beta"
            summary = ts.summarize(text, target_length=5)

        self.assertTrue(summary.endswith("."))
        self.assertIn("alpha", summary)
        self.assertIn("beta", summary)

    def test_summarize_respects_cluster_indices_order(self):
        fake_text_mod = types.SimpleNamespace(TfidfVectorizer=_FakeVectorizer)
        with patch.object(TextSummarizer, "_require_sklearn_module", return_value=fake_text_mod):
            ts = TextSummarizer()

        with patch.object(ts, "_get_sentence_vectors", return_value=(_FakeSparse([[1, 2], [2, 3], [3, 4]]), ["s1", "s2", "s3"])):
            with patch.object(ts, "_cluster_sentences", return_value=[2, 0]):
                summary = ts.summarize("ignored", target_length=2)

        self.assertEqual(summary, "s3. s1.")


if __name__ == "__main__":
    unittest.main()
