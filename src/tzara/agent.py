"""
LLM-backed agent for Tzara using LangChain + LangGraph + Ollama.
"""
from typing import Generator, TypedDict, Literal
from typing_extensions import Annotated

from langchain_ollama import ChatOllama
from langchain.messages import SystemMessage, AIMessage, AnyMessage
from langgraph.graph.message import add_messages

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from tzara.tools import calculator


class TzaraState(TypedDict):
    """
    State definition for Langraph for the Tzara agent
    """
    messages: Annotated[list[AnyMessage], add_messages]


class Tzara:
    """
    Main Tzara agent.
    """
    def __init__(self, model: str = "llama3.1:8b") -> None:
        self._model = model

        # Load the tools available to Tzara
        self._tools = [calculator.calculator]

        # Load the LLM
        self._llm = ChatOllama(
            model=model,
            temperature=0,
            streaming=True,
        ).bind_tools(self._tools)

        self._system_prompt = SystemMessage(
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

        # Build graph
        self._agent = self._build_agent()

    def _build_agent(self):
        """
        Builds the Langgraph for Tzara.
        """
        tool_node = ToolNode(self._tools)

        # Build workflow
        agent_builder = StateGraph(TzaraState)

        # Add nodes
        agent_builder.add_node("llm_call", self._llm_call)
        agent_builder.add_node("tool_node", tool_node)

        # Add edges to connect nodes
        agent_builder.add_edge(START, "llm_call")
        agent_builder.add_conditional_edges(
            "llm_call", 
            self._should_continue,
            ["tool_node", END]
            )
        
        agent_builder.add_edge("tool_node", "llm_call")

        return agent_builder.compile()

    def _llm_call(self, state: TzaraState) -> TzaraState:
        """
        Runs the LLM on the conversation state.
        """
        response = self._llm.invoke(state["messages"])
        return {"messages": [response]}

    def _should_continue(self, state: TzaraState) -> Literal["tool_node", END]:
        """Decide if we should continue the loop or stop based upon whether the LLM made a tool call"""

        messages = state["messages"]
        last_message = messages[-1]

        # If the LLM makes a tool call, then perform an action
        if last_message.tool_calls:
            return "tool_node"

        # Otherwise, we stop (reply to the user)
        return END

    def handle(self, text: str) -> Generator[str, None, None]:
        """
        Handle a single user input and stream assistant responses.
        """
        initial_state = {
            "messages": [
                self._system_prompt,
                {"role": "user", "content": text},
            ]
        }

        for message, metadata in self._agent.stream(
            initial_state,
            stream_mode="messages",
        ):
            # Only stream assistant messages
            if isinstance(message, AIMessage) and message.content:
                yield message.content

