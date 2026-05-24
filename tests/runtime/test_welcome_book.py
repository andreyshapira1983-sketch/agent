"""Tests for runtime.welcome_book.WelcomeBook — persistent greeting state."""

from __future__ import annotations

from runtime.welcome_book import WelcomeBook


def test_first_call_returns_true_subsequent_calls_false(tmp_path):
    book = WelcomeBook(tmp_path / "w.db")
    assert book.has_greeted("alice") is False
    assert book.mark_greeted("alice") is True
    assert book.has_greeted("alice") is True
    assert book.mark_greeted("alice") is False  # already there


def test_state_persists_across_reopens(tmp_path):
    p = tmp_path / "w.db"
    b1 = WelcomeBook(p)
    b1.mark_greeted("bob")
    b1.close()

    b2 = WelcomeBook(p)
    assert b2.has_greeted("bob") is True
    assert b2.mark_greeted("bob") is False


def test_distinct_clients_tracked_separately(tmp_path):
    book = WelcomeBook(tmp_path / "w.db")
    assert book.mark_greeted("alice") is True
    assert book.mark_greeted("bob") is True
    assert len(book) == 2


def test_forget_lets_us_greet_again(tmp_path):
    book = WelcomeBook(tmp_path / "w.db")
    book.mark_greeted("alice")
    book.forget("alice")
    assert book.has_greeted("alice") is False
    assert book.mark_greeted("alice") is True


def test_context_manager_closes_handle(tmp_path):
    with WelcomeBook(tmp_path / "w.db") as book:
        book.mark_greeted("x")
    # Re-open should still work — close was clean.
    book2 = WelcomeBook(tmp_path / "w.db")
    assert book2.has_greeted("x") is True
