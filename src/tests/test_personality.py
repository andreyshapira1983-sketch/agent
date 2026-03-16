from __future__ import annotations

from src.personality import emotion_matrix, emotional_reactions, personality, triggers
from src.personality.emotional_reactions import FLAVOR_TEMPLATES
from src.personality.personal_identity import PersonalIdentity


def _reset_emotions() -> None:
    emotion_matrix.set_state({k: 0.0 for k in emotion_matrix.EMOTION_KEYS})


def test_trigger_updates_emotion_state_without_random() -> None:
    _reset_emotions()
    triggers.fire_trigger("patch_failed", with_random=False)
    state = emotion_matrix.get_state()
    assert state["frustration"] > 0.0
    assert state["anxiety"] > 0.0


def test_emotional_flavor_for_high_frustration() -> None:
    _reset_emotions()
    emotion_matrix.set_state({"frustration": 0.8})
    flavor = emotional_reactions.get_emotional_flavor(threshold=0.35)
    assert flavor is not None
    assert flavor in FLAVOR_TEMPLATES["frustration"]["high"]


def test_get_whim_from_emotions_when_fatigue_high(monkeypatch) -> None:
    _reset_emotions()
    emotion_matrix.set_state({"fatigue": 0.8, "excitement": 0.0})

    monkeypatch.setattr("random.random", lambda: 0.0)
    whim = personality.get_whim_from_emotions()
    assert whim is not None
    assert "агента" in whim.lower() or "семейк" in whim.lower()


def test_personal_identity_introduce_returns_bio() -> None:
    identity = PersonalIdentity(name="Тестер", gender="технический")
    intro = identity.introduce()
    assert "Тестер" in intro
    assert "агент" in intro


def test_neutral_family_mode_disables_fantasy_whim(monkeypatch) -> None:
    _reset_emotions()
    monkeypatch.setenv("AGENT_NEUTRAL_FAMILY_MODE", "1")
    emotion_matrix.set_state({"fatigue": 0.8, "excitement": 0.0})
    monkeypatch.setattr("random.random", lambda: 0.0)

    whim = personality.get_whim_from_emotions()

    assert whim is not None
    low = whim.lower()
    assert "семей" not in low
    assert "женщ" not in low
    assert "дет" not in low


def test_neutral_family_mode_switches_personality_hint(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_NEUTRAL_FAMILY_MODE", "1")
    hint = personality.get_personality_hint()
    assert "нейтраль" in hint.lower() or "рабоч" in hint.lower()
    monkeypatch.delenv("AGENT_NEUTRAL_FAMILY_MODE", raising=False)