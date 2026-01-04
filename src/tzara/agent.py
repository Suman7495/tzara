"""
LLM-backed agent for Tzara using Ollama.
"""
from typing import Generator
from ollama import chat

class Tzara:
    """
    Core Tzara agent.

    Responsible for:
    - Sending prompts to the LLM
    - Streaming responses token-by-token
    """
    def __init__(self, model: str="gemma3") -> None:
        """
        Initialize the agent.

        Args:
            model: Ollama model name.
        """
        self._model = model

    def set_model(self, model: str) -> None:
        """
        Update the LLM model.

        Args:
            model: New Ollama model name.
        """
        self._model = model

    def handle(self, text: str) -> Generator[str, None, None]:
        """
        Stream LLM output as tokens.

        Handles Ollama 'thinking' and 'content' messages.

        Args:
            text: User input prompt.

        Yields:
            Output tokens as strings.
        """
        stream = chat(
            model=self._model,
            messages=[{'role': 'user','content': text}],
            stream=True,
        )

        in_thinking = False
        content = ''
        thinking = ''
        for chunk in stream:
            # Show partial thinking
            if chunk.message.thinking:
                if not in_thinking:
                    in_thinking = True
                    yield "I am thinking:\n"
                # accumulate the partial thinking 
                thinking += chunk.message.thinking
                yield chunk.message.thinking

            # Show final answer
            elif chunk.message.content:
                if in_thinking:
                    in_thinking = False
                content += chunk.message.content
                yield chunk.message.content
