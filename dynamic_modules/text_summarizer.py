# text_summarizer.py

import importlib

class TextSummarizer:
    """
    A utility class for summarizing long texts.
    
    Methods
    -------
    summarize(text: str, target_length: int) -> str
        Takes a long text and a target summary length, returning a summarized version.
    """

    def __init__(self):
        sklearn_text = self._require_sklearn_module("sklearn.feature_extraction.text")
        self.vectorizer = sklearn_text.TfidfVectorizer(stop_words='english')

    @staticmethod
    def _require_sklearn_module(module_name: str):
        """Loads sklearn module lazily and raises a clear install hint when missing."""
        try:
            return importlib.import_module(module_name)
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency 'scikit-learn'. Install it with: pip install scikit-learn"
            ) from exc
        
    def _get_sentence_vectors(self, text: str):
        """
        Converts the input text into sentence vectors using TF-IDF.

        Parameters
        ----------
        text : str
            The input text to be vectorized.

        Returns
        -------
        numpy.ndarray
            An array of sentence vectors.
        """
        sentences = text.split('. ')
        return self.vectorizer.fit_transform(sentences), sentences
    
    def _cluster_sentences(self, sentence_vectors, n_clusters: int):
        """
        Clusters sentences into groups using KMeans.

        Parameters
        ----------
        sentence_vectors : sparse matrix
            An array of sentence vectors (TF-IDF).
        n_clusters : int
            The number of clusters to form.

        Returns
        -------
        list[int]
            Indices of sentences closest to each cluster centroid.
        """
        sklearn_cluster = self._require_sklearn_module("sklearn.cluster")
        kmeans = sklearn_cluster.KMeans(n_clusters=n_clusters)
        kmeans.fit(sentence_vectors)
        dense = sentence_vectors.toarray()
        indices = []
        for center in kmeans.cluster_centers_:
            dists = ((dense - center) ** 2).sum(axis=1)
            indices.append(int(dists.argmin()))
        return indices

    def summarize(self, text: str, target_length: int) -> str:
        """
        Summarizes the input text to the desired length.

        Parameters
        ----------
        text : str
            The long text to summarize.
        target_length : int
            The desired number of sentences in the summary.

        Returns
        -------
        str
            The summarized text.
        """
        sentence_vectors, sentences = self._get_sentence_vectors(text)
        n_clusters = min(target_length, len(sentences))
        centroid_indices = self._cluster_sentences(sentence_vectors, n_clusters)

        summary_sentences = [sentences[i] for i in centroid_indices]

        return '. '.join(summary_sentences[:target_length]) + '.'

# Example usage:
# if __name__ == "__main__":
#     text = "Ваш длинный текст здесь."
#     summarizer = TextSummarizer()
#     print(summarizer.summarize(text, 3))