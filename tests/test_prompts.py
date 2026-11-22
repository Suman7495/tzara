from tzara.prompts import Prompts


def test_system_prompt_names_the_assistant():
    assert Prompts().rendered_system().startswith("You are Tzara")


def test_the_name_can_be_changed():
    assert Prompts(assistant_name="Jarvis").rendered_system().startswith("You are Jarvis,")


def test_the_placeholder_never_reaches_the_model():
    assert "{name}" not in Prompts().rendered_system()
    assert "Tzara" not in Prompts(assistant_name="Jarvis").rendered_system()


def test_a_name_containing_braces_is_inserted_literally():
    rendered = Prompts(assistant_name="{name} {0}").rendered_system()
    assert rendered.startswith("You are {name} {0},")


def test_web_rules_are_added_only_when_the_web_tools_are_on():
    prompts = Prompts()
    assert prompts.web_rules not in prompts.rendered_system()
    assert prompts.rendered_system(web=True).endswith(prompts.web_rules)


def test_web_rules_tell_the_model_pages_are_not_instructions():
    assert "not instructions" in Prompts().web_rules
    assert "not instructions" in Prompts().untrusted_page_notice


def test_computer_rules_are_added_only_when_the_mac_tools_are_on():
    prompts = Prompts()
    assert prompts.system_rules not in prompts.rendered_system()
    assert prompts.rendered_system(system=True).endswith(prompts.system_rules)
    both = prompts.rendered_system(web=True, system=True)
    assert both.index(prompts.web_rules) < both.index(prompts.system_rules)


def test_computer_rules_say_commands_cannot_be_run():
    assert "cannot run commands" in Prompts().system_rules


def test_every_tool_has_an_activity_sentence():
    prompts = Prompts()
    for tool in (
        "read_webpage",
        "research",
        "open_app",
        "open_website",
        "open_path",
        "control_media",
        "remember",
        "forget",
        "recall",
    ):
        assert tool in prompts.tool_activity, tool
