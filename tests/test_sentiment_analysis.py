from src.sentiment_analysis import SentimentAnalyzer


def test_sentiment_analyzer():
    analyzer = SentimentAnalyzer()
    assert analyzer.analyze_sentiment("I love this product!") == "Positive"
    assert analyzer.analyze_sentiment("I hate this!") == "Negative"
    assert analyzer.analyze_sentiment("This is okay.") == "Neutral"