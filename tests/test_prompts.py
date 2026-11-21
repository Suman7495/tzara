from tzara.prompts import Prompts

BUILT_IN_TOOLS = {"ls", "read_file", "write_file", "edit_file", "glob", "grep", "task"}


def test_describes_every_overridden_builtin_tool():
    assert set(Prompts().tool_descriptions) == BUILT_IN_TOOLS


def test_tool_descriptions_stay_short():
    # DeepAgents' own descriptions run to thousands of characters and slow
    # every cold start; the replacements must not creep back up.
    assert all(len(text) < 600 for text in Prompts().tool_descriptions.values())


def test_instances_do_not_share_the_description_mapping():
    first, second = Prompts(), Prompts()
    first.tool_descriptions["ls"] = "changed"  # type: ignore[index]
    assert second.tool_descriptions["ls"] == Prompts.DEFAULT_TOOL_DESCRIPTIONS["ls"]


def test_system_prompt_names_the_assistant():
    assert Prompts().system.startswith("You are Tzara")
