from typing import List, Optional, TypedDict

from langchain_core.messages import BaseMessage


class ChatState(TypedDict):
    messages: List[BaseMessage]
    summary: Optional[str]
