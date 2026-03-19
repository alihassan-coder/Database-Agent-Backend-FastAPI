"""
Chat API routes with streaming support and web search integration.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from agent.prompts import DEFAULT_SYSTEM_PROMPT, WEB_SEARCH_SYSTEM_PROMPT, NO_REPETITION_REMINDER
from agent.tools import (
    get_llm,
    summarize_history_text,
    needs_web_search,
    web_search,
    get_current_datetime,
)
from config.database_config import SessionLocal, get_db
from models import Chat, Message, User
from utils.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chats", tags=["chats"])


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class ChatSummaryOut(BaseModel):
    id: int
    title: Optional[str]
    created_at: datetime
    last_message_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatDetailOut(ChatSummaryOut):
    messages: List[MessageOut]


class MessageCreate(BaseModel):
    content: str


@router.get("/", response_model=List[ChatSummaryOut])
def list_chats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """List all chats for the current user."""
    chats = (
        db.query(Chat)
        .filter(Chat.user_id == current_user.id)
        .order_by(Chat.created_at.desc())
        .all()
    )
    return chats


@router.post("/", response_model=ChatSummaryOut, status_code=status.HTTP_201_CREATED)
def create_chat(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Create a new chat."""
    chat = Chat(user_id=current_user.id)
    db.add(chat)
    db.commit()
    db.refresh(chat)
    return chat


def _get_chat_or_404(db: Session, current_user: User, chat_id: int) -> Chat:
    """Get chat by ID or raise 404."""
    chat = (
        db.query(Chat)
        .filter(Chat.id == chat_id, Chat.user_id == current_user.id)
        .first()
    )
    if not chat:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat not found"
        )
    return chat


@router.get("/{chat_id}", response_model=ChatDetailOut)
def get_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get chat details with messages."""
    chat = _get_chat_or_404(db, current_user, chat_id)
    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.position)
        .all()
    )
    return ChatDetailOut(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        last_message_at=chat.last_message_at,
        messages=messages,
    )


def _build_conversation_context(history_pairs: List[tuple], summary: Optional[str]) -> str:
    """Build context string from history for search decision."""
    context_parts = []
    if summary:
        context_parts.append(f"Summary: {summary[:200]}")
    
    # Last 3 exchanges for context
    recent = history_pairs[-6:] if len(history_pairs) > 6 else history_pairs
    for role, content in recent:
        context_parts.append(f"{role}: {content[:100]}")
    
    return "\n".join(context_parts)


def _build_llm_messages(
    system_content: str,
    history_pairs: List[tuple],
    search_results: Optional[str] = None,
) -> List:
    """Build message list for LLM."""
    messages = [SystemMessage(content=system_content)]
    
    # Add web search context if available
    if search_results:
        search_prompt = WEB_SEARCH_SYSTEM_PROMPT.format(
            current_datetime=get_current_datetime(),
            search_results=search_results,
        )
        messages.append(SystemMessage(content=search_prompt))
    
    # Add conversation history
    for role, content in history_pairs:
        if role == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    
    # Add anti-repetition reminder for longer conversations
    if len(history_pairs) > 4:
        messages.append(SystemMessage(content=NO_REPETITION_REMINDER))
    
    return messages


@router.post("/{chat_id}/messages/stream")
def send_message_stream(
    chat_id: int,
    message_in: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Stream assistant reply with optional web search."""
    chat = _get_chat_or_404(db, current_user, chat_id)

    content = message_in.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message content cannot be empty",
        )

    # Store user message
    last_position = (
        db.query(func.max(Message.position))
        .filter(Message.chat_id == chat.id)
        .scalar()
        or 0
    )

    user_msg = Message(
        chat_id=chat.id,
        role="user",
        content=content,
        position=last_position + 1,
    )
    db.add(user_msg)
    db.flush()

    # Load messages for context
    all_messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.position)
        .all()
    )

    # Update summary if conversation is long
    if len(all_messages) > 6:
        older_messages = all_messages[:-6]
        history_text = "\n".join(f"{m.role}: {m.content}" for m in older_messages)
        try:
            chat.summary = summarize_history_text(history_text, chat.summary)
        except Exception as e:
            logger.error(f"Summary update failed: {e}")

    # Get recent history for LLM
    recent_messages = all_messages[-6:]
    history_pairs = [(m.role, m.content) for m in recent_messages]

    # Set chat title
    if chat.title is None:
        chat.title = content[:60]

    db.commit()

    # Build context for search decision
    conversation_context = _build_conversation_context(history_pairs, chat.summary)
    
    # Decide on web search
    use_web_search, search_query = needs_web_search(content, conversation_context)
    search_results = None
    
    if use_web_search and search_query:
        logger.info(f"Performing web search: {search_query}")
        search_results = web_search(search_query)
        if not search_results:
            logger.warning("Web search returned no results")
            use_web_search = False

    # Build system prompt
    system_content = DEFAULT_SYSTEM_PROMPT
    if chat.summary:
        system_content += f"\n\nConversation summary:\n{chat.summary}"

    # Store values for generator closure
    user_id = current_user.id
    chat_id_value = chat.id

    def token_generator():
        """Stream tokens and save response."""
        try:
            llm = get_llm()
            
            llm_messages = _build_llm_messages(
                system_content,
                history_pairs,
                search_results,
            )

            chunks = []
            
            # Show web search indicator
            if use_web_search and search_results:
                prefix = "🔍 *Using web search results*\n\n"
                chunks.append(prefix)
                yield prefix

            # Stream response
            for chunk in llm.stream(llm_messages):
                text = getattr(chunk, "content", "") or ""
                if text:
                    chunks.append(text)
                    yield text

            # Save response
            full_text = "".join(chunks).strip()
            if full_text:
                _save_assistant_message(user_id, chat_id_value, full_text)
                
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            error_msg = "Sorry, I encountered an error. Please try again."
            yield error_msg
            _save_assistant_message(user_id, chat_id_value, error_msg)

    headers = {"X-Web-Search-Used": "true" if use_web_search else "false"}
    return StreamingResponse(
        token_generator(),
        media_type="text/plain",
        headers=headers,
    )


def _save_assistant_message(user_id: int, chat_id: int, content: str):
    """Save assistant message to database."""
    db = SessionLocal()
    try:
        chat = (
            db.query(Chat)
            .filter(Chat.id == chat_id, Chat.user_id == user_id)
            .first()
        )
        if not chat:
            return

        last_pos = (
            db.query(func.max(Message.position))
            .filter(Message.chat_id == chat.id)
            .scalar()
            or 0
        )

        assistant_msg = Message(
            chat_id=chat.id,
            role="assistant",
            content=content,
            position=last_pos + 1,
        )
        db.add(assistant_msg)
        chat.last_message_at = datetime.now(timezone.utc)
        db.commit()
        
    except Exception as e:
        logger.error(f"Failed to save message: {e}")
        db.rollback()
    finally:
        db.close()


@router.delete("/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_chat(
    chat_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete a chat and its messages."""
    chat = _get_chat_or_404(db, current_user, chat_id)
    db.delete(chat)
    db.commit()
    return None


@router.post("/{chat_id}/messages", response_model=ChatDetailOut)
def send_message(
    chat_id: int,
    message_in: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send message and get response (non-streaming)."""
    chat = _get_chat_or_404(db, current_user, chat_id)

    content = message_in.content.strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Message content cannot be empty",
        )

    last_position = (
        db.query(func.max(Message.position))
        .filter(Message.chat_id == chat.id)
        .scalar()
        or 0
    )

    # Save user message
    user_msg = Message(
        chat_id=chat.id,
        role="user",
        content=content,
        position=last_position + 1,
    )
    db.add(user_msg)

    # Load messages
    all_messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.position)
        .all()
    )

    # Update summary if needed
    if len(all_messages) > 6:
        older_messages = all_messages[:-6]
        history_text = "\n".join(f"{m.role}: {m.content}" for m in older_messages)
        try:
            chat.summary = summarize_history_text(history_text, chat.summary)
        except Exception as e:
            logger.error(f"Summary update failed: {e}")

    recent_messages = all_messages[-6:]
    history_pairs = [(m.role, m.content) for m in recent_messages]

    # Build context and check for web search
    conversation_context = _build_conversation_context(history_pairs, chat.summary)
    use_web_search, search_query = needs_web_search(content, conversation_context)
    search_results = None
    
    if use_web_search and search_query:
        search_results = web_search(search_query)

    # Build system prompt
    system_content = DEFAULT_SYSTEM_PROMPT
    if chat.summary:
        system_content += f"\n\nConversation summary:\n{chat.summary}"

    # Get LLM response
    try:
        llm = get_llm()
        llm_messages = _build_llm_messages(
            system_content,
            history_pairs,
            search_results,
        )
        response = llm.invoke(llm_messages)
        assistant_content = response.content or "I couldn't generate a response."
        
        if use_web_search and search_results:
            assistant_content = "🔍 *Using web search results*\n\n" + assistant_content
            
    except Exception as e:
        logger.error(f"LLM error: {e}")
        assistant_content = "Sorry, I encountered an error. Please try again."

    # Save assistant message
    assistant_msg = Message(
        chat_id=chat.id,
        role="assistant",
        content=assistant_content,
        position=last_position + 2,
    )
    db.add(assistant_msg)

    # Update chat metadata
    if chat.title is None:
        chat.title = content[:60]
    chat.last_message_at = datetime.now(timezone.utc)

    db.commit()

    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat.id)
        .order_by(Message.position)
        .all()
    )

    return ChatDetailOut(
        id=chat.id,
        title=chat.title,
        created_at=chat.created_at,
        last_message_at=chat.last_message_at,
        messages=messages,
    )
