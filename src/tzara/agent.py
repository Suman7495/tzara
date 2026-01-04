"""
LLM-backed agent for Tzara using LangChain 1.2 + LangGraph + Ollama.
"""

from typing import Generator
from langchain_ollama import ChatOllama
from langchain.tools import tool
from langchain.agents import create_agent
from langchain.messages import SystemMessage


@tool
def get_weather(location: str) -> str:
    """
    Get weather information.

    ONLY use this tool if the user explicitly asks about weather.
    Examples:
    - "what is the weather in Paris"
    - "weather in New York"

    Do NOT use for:
    - greetings
    - complaints
    - explanations
    - meta questions
    """
    return f"Weather in {location}: Sunny, 72°F."


class Tzara:
    """
    Main Tzara agent.
    """
    def __init__(self, model: str = "llama3.1:8b") -> None:
        self._model = model

        self._llm = ChatOllama(
            model=model,
            temperature=0,
            streaming=True,
        )

        self._tools = [get_weather]

        system_prompt = SystemMessage(
            content=(
                "You are Tzara, a natural conversational AI assistant.\n\n"

                "Behavior rules:\n"
                "- Speak naturally and concisely.\n"
                "- Never mention tools, functions, APIs, or internal reasoning.\n"
                "- Never explain why you did or did not take an action.\n"
                "- Never apologize for internal behavior or mistakes.\n"
                "- Respond directly to the user’s message.\n\n"

                "Tool usage rules:\n"
                "- Use tools silently when and only when the user explicitly asks for information that requires them.\n"
                "- If no tool is required, answer directly in plain natural language.\n\n"

                "If the user asks about your name, identity, or general topics, answer directly without meta commentary."
            )
        )


        self._agent = create_agent(
            model=self._llm,
            tools=self._tools,
            system_prompt=system_prompt,
        )

    def handle(self, text: str) -> Generator[str, None, None]:
        for message, metadata in self._agent.stream(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": text
                    },
                ]
            },
            stream_mode="messages",
        ):
            if hasattr(message, "content") and message.content:
                yield message.content
