from uuid import uuid4

from tzara.agent.summaries import SummaryRunFilter


def start(handler: SummaryRunFilter, run_id, metadata):
    handler.on_chat_model_start({}, [[]], run_id=run_id, metadata=metadata)


def test_flags_messages_from_summarization_runs():
    handler, run_id = SummaryRunFilter(), uuid4()
    start(handler, run_id, {"lc_source": "summarization"})
    assert handler.is_summary(f"lc_run--{run_id}")


def test_ordinary_runs_are_not_flagged():
    handler, run_id = SummaryRunFilter(), uuid4()
    start(handler, run_id, {"langgraph_node": "model"})
    assert not handler.is_summary(f"lc_run--{run_id}")


def test_runs_without_metadata_are_not_flagged():
    handler, run_id = SummaryRunFilter(), uuid4()
    start(handler, run_id, None)
    assert not handler.is_summary(f"lc_run--{run_id}")


def test_only_the_summarization_run_is_flagged():
    handler, summary_run, reply_run = SummaryRunFilter(), uuid4(), uuid4()
    start(handler, summary_run, {"lc_source": "summarization"})
    start(handler, reply_run, {})
    assert handler.is_summary(f"lc_run--{summary_run}")
    assert not handler.is_summary(f"lc_run--{reply_run}")


def test_unknown_or_missing_ids_are_not_flagged():
    handler = SummaryRunFilter()
    assert not handler.is_summary(None)
    assert not handler.is_summary("")
    assert not handler.is_summary(f"lc_run--{uuid4()}")
