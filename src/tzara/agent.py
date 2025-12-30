from ollama import chat
from ollama import ChatResponse

class Tzara:
    def __init__(self, model="gemma3"):
        self._model = model

    def set_model(self, model):
        self._model = model

    def handle(self, text):
        """
        Generator yielding tokens for real-time CLI display.
        Handles Ollama 'thinking' vs 'content' messages.
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
