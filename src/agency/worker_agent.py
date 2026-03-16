"""
Воркер дочернего агента: отдельный процесс, свой контекст (agent_id, эмоции от родителя), один цикл оркестратора + опционально inbox.
Запуск: python -m src.agency.worker_agent (AGENT_ID, AGENT_PARENT_ID, AGENT_ROLE, AGENT_NAME, AGENT_GENERATION в env).
"""
from __future__ import annotations

import os
import sys
import threading
import time

# Project root
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)

def main() -> None:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_root, ".env"))
    agent_id = os.environ.get("AGENT_ID", "root")
    parent_id = os.environ.get("AGENT_PARENT_ID", "")
    role = os.environ.get("AGENT_ROLE", "")
    name = os.environ.get("AGENT_NAME", "Agent")
    generation = int(os.environ.get("AGENT_GENERATION", "1"))

    from src.state.agent_state import set_state as set_agent_state
    set_agent_state("agent_id", agent_id)

    stop_heartbeat = threading.Event()

    def _heartbeat_loop() -> None:
        while not stop_heartbeat.wait(2.0):
            try:
                from src.agency.family_store import update_runtime_state
                update_runtime_state(
                    agent_id,
                    {
                        "status": "running",
                        "heartbeat_at": time.time(),
                    },
                )
            except Exception:
                pass

    heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)

    try:
        from src.agency.family_store import update_runtime_state
        update_runtime_state(
            agent_id,
            {
                "status": "running",
                "pid": os.getpid(),
                "parent_id": parent_id,
                "role": role,
                "name": name,
                "generation": generation,
                "started_at": time.time(),
                "heartbeat_at": time.time(),
            },
        )
    except Exception:
        pass

    heartbeat_thread.start()

    # Наследование эмоций от родителя
    try:
        from src.agency.family_store import read_emotion_init
        from src.personality.emotion_matrix import set_state as set_emotion_state
        init = read_emotion_init(agent_id)
        if init:
            set_emotion_state(init)
    except Exception:
        pass

    # Один цикл оркестратора (очередь задач + observe включает inbox для дочерних агентов)
    try:
        from src.tools.orchestrator import Orchestrator
        orch = Orchestrator()
        summary = orch.run_cycle()
        try:
            from src.agency.family_store import update_runtime_state
            update_runtime_state(
                agent_id,
                {
                    "status": "completed",
                    "finished_at": time.time(),
                    "heartbeat_at": time.time(),
                    "last_cycle": {
                        "status": summary.get("status"),
                        "goal": summary.get("goal"),
                        "outcomes_count": summary.get("outcomes_count"),
                    },
                },
            )
        except Exception:
            pass
    except Exception as e:
        try:
            from src.agency.family_store import update_runtime_state
            update_runtime_state(
                agent_id,
                {
                    "status": "error",
                    "finished_at": time.time(),
                    "heartbeat_at": time.time(),
                    "last_error": str(e)[:300],
                },
            )
        except Exception:
            pass
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=0.2)


if __name__ == "__main__":
    main()
