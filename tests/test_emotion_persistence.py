from __future__ import annotations

import json

from src.personality import emotion_matrix


def test_set_state_persists_to_disk(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "emotion_state.json"
    monkeypatch.setattr(emotion_matrix, "_STATE_PATH", state_path)

    emotion_matrix.set_state({"frustration": 0.6, "curiosity": 0.4})

    assert state_path.exists()
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert raw.get("frustration") == 0.6
    assert raw.get("curiosity") == 0.4


def test_load_state_reads_persisted_values(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "emotion_state.json"
    state_path.write_text(
        json.dumps({"frustration": 0.9, "fatigue": 0.2}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(emotion_matrix, "_STATE_PATH", state_path)

    for key in emotion_matrix.EMOTION_KEYS:
        emotion_matrix._state[key] = 0.0
    emotion_matrix._load_state()
    state = emotion_matrix.get_state()

    assert state["frustration"] == 0.9
    assert state["fatigue"] == 0.2
