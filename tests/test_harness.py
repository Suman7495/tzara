from unittest.mock import patch

from langchain.agents.middleware import TodoListMiddleware

from tzara.agent.harness import ConciseTodoListMiddleware, HarnessConfigurator
from tzara.prompts import Prompts


def test_installs_one_profile_per_provider():
    prompts = Prompts()
    with patch("tzara.agent.harness.register_harness_profile") as register:
        HarnessConfigurator(prompts, ["ollama", "openai"]).install_profiles()

    assert [call.args[0] for call in register.call_args_list] == ["ollama", "openai"]
    profile = register.call_args_list[0].args[1]
    assert profile.base_system_prompt == prompts.base
    assert dict(profile.tool_description_overrides) == dict(prompts.tool_descriptions)
    assert profile.excluded_middleware == frozenset({"TodoListMiddleware"})


def test_replacement_todo_middleware_uses_configured_text():
    prompts = Prompts(todo_system="SYSTEM TEXT", todo_tool="TOOL TEXT")
    (middleware,) = HarnessConfigurator(prompts, []).middleware()

    assert isinstance(middleware, ConciseTodoListMiddleware)
    assert middleware.system_prompt == "SYSTEM TEXT"
    assert middleware.tool_description == "TOOL TEXT"


def test_replacement_is_not_matched_by_the_exclusion_of_the_original():
    """The exclusion works by class name, so the replacement needs its own."""
    (middleware,) = HarnessConfigurator(Prompts(), []).middleware()

    assert isinstance(middleware, TodoListMiddleware)
    assert middleware.name not in HarnessConfigurator.EXCLUDED_MIDDLEWARE
