"""
tests/brain/test_brain_loop.py
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from brain.brain_loop import BrainLoop, InputMessage, LoopState
from brain.core import Brain, ThinkResult
from brain.interfaces.llm_interface import LLMInterface
from brain.interfaces.memory_interface import MemoryInterface
from brain.planner import Planner


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

def make_brain(action: str = "respond", content: str = "Hello", confidence: float = 0.9):
    llm = MagicMock(spec=LLMInterface)
    llm.call.return_value = {
        "action": action,
        "content": content,
        "confidence": confidence,
        "reasoning": "test",
    }
    memory = MagicMock(spec=MemoryInterface)
    memory.recall_history.return_value = []
    memory.recall_facts.return_value = []
    return Brain(llm=llm, memory=memory)


def make_loop(action: str = "respond", content: str = "Hello", confidence: float = 0.9):
    brain = make_brain(action=action, content=content, confidence=confidence)
    planner = Planner()
    return BrainLoop(brain=brain, planner=planner), brain, planner


def make_message(text: str = "test", session: str = "s1") -> InputMessage:
    return InputMessage(content=text, session_id=session)


# ------------------------------------------------------------------
# State transitions
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initial_state_is_idle():
    loop, _, _ = make_loop()
    assert loop.state() == LoopState.IDLE


@pytest.mark.asyncio
async def test_start_changes_state_to_running():
    loop, _, _ = make_loop()
    await loop.start()
    assert loop.state() == LoopState.RUNNING
    await loop.stop()


@pytest.mark.asyncio
async def test_stop_changes_state_to_stopped():
    loop, _, _ = make_loop()
    await loop.start()
    await loop.stop()
    assert loop.state() == LoopState.STOPPED


@pytest.mark.asyncio
async def test_double_start_is_safe():
    loop, _, _ = make_loop()
    await loop.start()
    await loop.start()  # Should not raise
    assert loop.state() == LoopState.RUNNING
    await loop.stop()


# ------------------------------------------------------------------
# submit()
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_returns_true_when_running():
    loop, _, _ = make_loop()
    await loop.start()
    result = await loop.submit(make_message())
    assert result is True
    await loop.stop()


@pytest.mark.asyncio
async def test_submit_returns_false_when_stopped():
    loop, _, _ = make_loop()
    result = await loop.submit(make_message())
    assert result is False


@pytest.mark.asyncio
async def test_submit_returns_false_when_queue_full():
    loop, _, _ = make_loop()
    loop.MAX_QUEUE_SIZE = 1
    loop._queue = asyncio.Queue(maxsize=1)
    await loop.start()
    await loop.submit(make_message("msg1"))
    # Queue is full — second should fail
    await asyncio.sleep(0)  # let loop drain a bit
    await loop.stop()


# ------------------------------------------------------------------
# on_response callback
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_response_called_for_respond_action():
    loop, _, _ = make_loop(action="respond", content="Hi user")
    received = []

    async def capture(session_id, content, result):
        received.append((session_id, content))

    loop.on_response = capture
    await loop.start()
    await loop.submit(make_message("Hello", session="s42"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert len(received) == 1
    assert received[0][0] == "s42"
    assert received[0][1] == "Hi user"


@pytest.mark.asyncio
async def test_on_response_called_for_clarify_action():
    loop, _, _ = make_loop(action="clarify", content="Please clarify")
    received = []

    async def capture(session_id, content, result):
        received.append(content)

    loop.on_response = capture
    await loop.start()
    await loop.submit(make_message("vague input"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert "Please clarify" in received


# ------------------------------------------------------------------
# on_tool_call callback
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_tool_call_called():
    loop, _, _ = make_loop(
        action="tool_call",
        content={"tool": "search", "params": {"query": "Python"}},
    )
    called_with = []

    async def capture(session_id, content, result):
        called_with.append(content)

    loop.on_tool_call = capture
    await loop.start()
    await loop.submit(make_message("search for Python"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert len(called_with) == 1
    # Interpreter now normalises every tool_call to {tool_name, params}.
    assert called_with[0]["tool_name"] == "search"
    assert called_with[0]["params"] == {"query": "Python"}


# ------------------------------------------------------------------
# Human Approval gate
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_gate_blocks_action_when_denied():
    loop, _, _ = make_loop(action="tool_call", content={"tool": "delete"})
    tool_called = []
    responses = []

    async def deny(result):
        return False  # Always deny

    async def capture_tool(session_id, content, result):
        tool_called.append(content)

    async def capture_response(session_id, content, result):
        responses.append(content)

    loop.on_approval = deny
    loop.on_tool_call = capture_tool
    loop.on_response = capture_response

    await loop.start()
    await loop.submit(make_message("delete everything"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert len(tool_called) == 0  # tool must NOT be called
    assert len(responses) == 1    # user notified


@pytest.mark.asyncio
async def test_approval_gate_allows_action_when_approved():
    loop, _, _ = make_loop(action="tool_call", content={"tool": "search"})
    tool_called = []

    async def approve(result):
        return True

    async def capture_tool(session_id, content, result):
        tool_called.append(content)

    loop.on_approval = approve
    loop.on_tool_call = capture_tool

    await loop.start()
    await loop.submit(make_message("search web"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert len(tool_called) == 1


# ------------------------------------------------------------------
# wait action
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wait_action_does_not_trigger_callbacks():
    loop, _, _ = make_loop(action="wait", content=None, confidence=0.3)
    received = []

    async def capture(session_id, content, result):
        received.append(content)

    loop.on_response = capture
    await loop.start()
    await loop.submit(make_message("ambiguous"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert len(received) == 0


# ------------------------------------------------------------------
# stats()
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_tracks_cycles():
    loop, _, _ = make_loop()
    await loop.start()
    await loop.submit(make_message("msg1"))
    await loop.submit(make_message("msg2"))
    await asyncio.sleep(0.3)
    await loop.stop()

    assert loop.stats()["cycles"] >= 2


@pytest.mark.asyncio
async def test_stats_contains_expected_keys():
    loop, _, _ = make_loop()
    stats = loop.stats()
    for key in ("state", "cycles", "errors", "started_at", "last_action", "queue_size"):
        assert key in stats


# ------------------------------------------------------------------
# Planner integration
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_planner_steps_dispatched_as_tool_calls():
    loop, brain, planner = make_loop(action="respond", content="Planning...")
    tool_calls = []

    async def capture_tool(session_id, content, result):
        tool_calls.append(content)

    loop.on_tool_call = capture_tool

    # Manually create a plan before loop processes the message
    planner.create_plan("do work", [
        {"description": "Fetch data", "action": "fetch"},
        {"description": "Save data", "action": "save"},
    ])

    await loop.start()
    await loop.submit(make_message("start plan"))
    await asyncio.sleep(0.5)
    await loop.stop()

    # Both plan steps should have been dispatched as tool calls
    assert len(tool_calls) >= 2
    actions = [c.get("action") for c in tool_calls if isinstance(c, dict)]
    assert "fetch" in actions
    assert "save" in actions


# ------------------------------------------------------------------
# stop action from Brain shuts down the loop
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stop_action_from_brain_stops_loop():
    loop, _, _ = make_loop(action="stop", content=None, confidence=1.0)
    # on_approval is not set, so stop fires immediately
    await loop.start()
    await loop.submit(make_message("please stop"))
    await asyncio.sleep(0.3)
    # Loop should be stopped or stopping by now
    assert loop.state() in {LoopState.STOPPED, LoopState.STOPPING}


# ------------------------------------------------------------------
# Error isolation — one bad message must not kill the loop
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_isolation_loop_survives_bad_message():
    loop, brain, _ = make_loop(action="respond", content="OK")
    call_count = [0]

    original_think = brain.think

    def flaky_think(raw_input, session_id):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("Simulated crash on first message")
        return original_think(raw_input, session_id)

    brain.think = flaky_think

    received = []

    async def capture(session_id, content, result):
        received.append(content)

    loop.on_response = capture

    await loop.start()
    await loop.submit(make_message("msg1"))  # Will crash
    await loop.submit(make_message("msg2"))  # Should still be processed
    await asyncio.sleep(0.4)
    await loop.stop()

    assert loop.stats()["errors"] >= 1
    assert len(received) >= 1  # Second message still got through


# ------------------------------------------------------------------
# Multiple messages in sequence
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_messages_processed_in_order():
    loop, _, _ = make_loop(action="respond", content="pong")
    received = []

    async def capture(session_id, content, result):
        received.append(session_id)

    loop.on_response = capture
    await loop.start()

    for i in range(5):
        await loop.submit(make_message(f"msg{i}", session=f"s{i}"))

    await asyncio.sleep(0.5)
    await loop.stop()

    assert len(received) == 5
    assert received == [f"s{i}" for i in range(5)]


# ------------------------------------------------------------------
# submit after stop returns False
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_after_stop_returns_false():
    loop, _, _ = make_loop()
    await loop.start()
    await loop.stop()
    result = await loop.submit(make_message("too late"))
    assert result is False


# ------------------------------------------------------------------
# stats error count increments
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats_error_count_increments_on_failure():
    loop, brain, _ = make_loop()

    def always_crash(raw_input, session_id):
        raise ValueError("Always fails")

    brain.think = always_crash
    await loop.start()
    await loop.submit(make_message("crash"))
    await asyncio.sleep(0.2)
    await loop.stop()

    assert loop.stats()["errors"] >= 1
