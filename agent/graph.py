"""
LangGraph chat agent configuration.
"""

from typing import List, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph

from .prompts import DEFAULT_SYSTEM_PROMPT
from .state import ChatState
from .tools import get_llm


def _call_model(state: ChatState) -> ChatState:
    """Call LLM with current state."""
    llm = get_llm()
    response = llm.invoke(state["messages"])
    messages = list(state["messages"]) + [response]
    return {"messages": messages, "summary": state.get("summary")}


# Build graph
_graph_builder = StateGraph(ChatState)
_graph_builder.add_node("model", _call_model)
_graph_builder.set_entry_point("model")
_graph_builder.set_finish_point("model")
chat_graph = _graph_builder.compile()


def generate_reply(summary: str | None, history: List[Tuple[str, str]]) -> str:
    """
    Generate assistant reply given summary and recent history.
    
    Args:
        summary: Optional conversation summary
        history: List of (role, content) tuples
        
    Returns:
        Assistant response text
    """
    messages = []
    
    # Build system prompt
    system_content = DEFAULT_SYSTEM_PROMPT
    if summary:
        system_content += f"\n\nConversation summary:\n{summary}"
    messages.append(SystemMessage(content=system_content))

    # Add history
    for role, content in history:
        if role == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))

    # Run graph
    state: ChatState = {"messages": messages, "summary": summary}
    result = chat_graph.invoke(state)
    
    ai_msg = result["messages"][-1]
    return ai_msg.content or ""
