from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from tzara.agent.memory import ConversationMemory


def thread_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def save_checkpoint(memory: ConversationMemory, thread_id: str) -> None:
    """Write a minimal checkpoint so tests can see whether a thread exists."""
    memory.checkpointer.put(
        thread_config(thread_id), empty_checkpoint(), {"source": "input", "step": 0}, {}
    )


def has_thread(memory: ConversationMemory, thread_id: str) -> bool:
    return memory.checkpointer.get_tuple(thread_config(thread_id)) is not None


def test_run_config_points_at_the_current_thread():
    memory = ConversationMemory()
    assert memory.run_config == {"configurable": {"thread_id": memory.thread_id}}


def test_each_conversation_gets_its_own_thread():
    assert ConversationMemory().thread_id != ConversationMemory().thread_id


def test_uses_the_checkpointer_it_is_given():
    saver = InMemorySaver()
    assert ConversationMemory(saver).checkpointer is saver


def test_reset_starts_a_new_thread_and_drops_the_old_history():
    memory = ConversationMemory()
    old = memory.thread_id
    save_checkpoint(memory, old)
    assert has_thread(memory, old)

    memory.reset()

    assert memory.thread_id != old
    assert not has_thread(memory, old)


def test_scratch_conversation_is_separate_and_removed_afterwards():
    memory = ConversationMemory()
    with memory.scratch() as config:
        scratch_id = config["configurable"]["thread_id"]
        assert scratch_id != memory.thread_id
        save_checkpoint(memory, scratch_id)
        assert has_thread(memory, scratch_id)
    assert not has_thread(memory, scratch_id)


def test_scratch_conversation_is_removed_even_if_the_body_fails():
    memory = ConversationMemory()
    try:
        with memory.scratch() as config:
            scratch_id = config["configurable"]["thread_id"]
            save_checkpoint(memory, scratch_id)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert not has_thread(memory, scratch_id)


def test_scratch_leaves_the_real_conversation_alone():
    memory = ConversationMemory()
    save_checkpoint(memory, memory.thread_id)
    with memory.scratch():
        pass
    assert has_thread(memory, memory.thread_id)
