"""
Web search and LLM tools for the chatbot agent.
Production-ready with proper error handling.
"""

import os
import logging
from typing import Optional, Tuple
from datetime import datetime, timezone

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from tavily import TavilyClient
from dotenv import load_dotenv

from .prompts import SUMMARY_SYSTEM_PROMPT

load_dotenv()

logger = logging.getLogger(__name__)

# Initialize Tavily client (optional - graceful degradation if not available)
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
tavily_client = TavilyClient(api_key=TAVILY_API_KEY) if TAVILY_API_KEY else None


def get_llm() -> ChatGroq:
    """Get configured LLM instance."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set")

    model_name = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    return ChatGroq(
        groq_api_key=api_key,
        model_name=model_name,
        temperature=0.7,
        max_tokens=2048,
    )


# Keywords that strongly indicate need for real-time information
REALTIME_KEYWORDS = {
    # Time-sensitive
    "today", "yesterday", "tomorrow", "now", "current", "latest", "recent",
    "this week", "this month", "this year", "2024", "2025",
    # News & events
    "news", "breaking", "update", "announcement", "happened", "happening",
    # Prices & markets
    "price", "cost", "rate", "stock", "crypto", "bitcoin", "dollar", "exchange",
    # Weather
    "weather", "temperature", "forecast", "rain", "snow",
    # Sports
    "score", "match", "game", "won", "lost", "playing",
    # Live events
    "live", "streaming", "event", "concert", "show",
    # Research & facts
    "who is", "what is", "how to", "where is", "when did", "why did",
    "statistics", "data", "research", "study", "report",
}

# Topics that typically need web search
SEARCH_TOPICS = {
    "celebrity", "politician", "company", "product", "movie", "song", "album",
    "country", "city", "person", "athlete", "team", "organization",
}


def needs_web_search(user_query: str, conversation_context: Optional[str] = None) -> Tuple[bool, str]:
    """
    Determine if query needs web search and optimize the search query.
    
    Returns:
        Tuple of (should_search: bool, optimized_query: str)
    """
    if not user_query or not tavily_client:
        return False, ""

    query_lower = user_query.lower().strip()
    
    # Skip very short queries or greetings
    if len(query_lower) < 10:
        greetings = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "bye", "goodbye"}
        if any(query_lower.startswith(g) for g in greetings):
            return False, ""
    
    # Check for explicit search request
    explicit_search = any(phrase in query_lower for phrase in [
        "search for", "look up", "find out", "google", "search the web",
        "what's the latest", "tell me about", "can you find",
    ])
    
    if explicit_search:
        return True, _optimize_search_query(user_query)
    
    # Check for realtime keywords
    has_realtime_keyword = any(kw in query_lower for kw in REALTIME_KEYWORDS)
    
    # Check for search topics
    has_search_topic = any(topic in query_lower for topic in SEARCH_TOPICS)
    
    # Quick decision based on keywords
    if has_realtime_keyword or has_search_topic:
        return True, _optimize_search_query(user_query)
    
    # Use LLM classifier for ambiguous cases
    try:
        should_search = _llm_classify_search_need(user_query, conversation_context)
        if should_search:
            return True, _optimize_search_query(user_query)
    except Exception as e:
        logger.warning(f"LLM classifier failed: {e}")
    
    return False, ""


def _optimize_search_query(user_query: str) -> str:
    """
    Optimize user query for better web search results.
    Removes conversational fluff and focuses on key terms.
    """
    # Remove common conversational prefixes
    prefixes_to_remove = [
        "can you tell me", "could you tell me", "please tell me",
        "i want to know", "i need to know", "what do you know about",
        "can you search for", "please search", "search for",
        "can you find", "please find", "look up",
        "what is the", "what are the", "who is the", "where is the",
        "tell me about", "explain", "describe",
    ]
    
    query = user_query.lower().strip()
    for prefix in prefixes_to_remove:
        if query.startswith(prefix):
            query = query[len(prefix):].strip()
            break
    
    # Remove trailing question marks and clean up
    query = query.rstrip("?").strip()
    
    # Add current year for time-sensitive queries
    time_sensitive = ["latest", "current", "recent", "now", "today"]
    if any(kw in query for kw in time_sensitive):
        current_year = datetime.now().year
        if str(current_year) not in query:
            query = f"{query} {current_year}"
    
    # Capitalize properly for search
    return query.strip() if query else user_query


def _llm_classify_search_need(user_query: str, context: Optional[str] = None) -> bool:
    """Use LLM to classify if query needs web search."""
    try:
        llm = get_llm()
        
        system_prompt = """You are a classifier that determines if a question needs current web information.

Answer YES if the question:
- Asks about recent events, news, or updates
- Asks about current prices, rates, or statistics
- Asks about real people, companies, or organizations
- Asks about facts that may have changed recently
- Asks about something you might not have accurate information about

Answer NO if the question:
- Is a greeting or casual conversation
- Asks about general knowledge that doesn't change
- Asks about personal opinions or advice
- Is about the conversation itself
- Can be answered with common knowledge

Reply with ONLY 'YES' or 'NO'."""

        user_prompt = f"Question: {user_query}"
        if context:
            user_prompt += f"\n\nConversation context: {context[:500]}"
        
        result = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt)
        ])
        
        answer = (result.content or "").strip().upper()
        return answer.startswith("Y")
        
    except Exception as e:
        logger.warning(f"Search classifier error: {e}")
        return False


def web_search(query: str, max_results: int = 5) -> Optional[str]:
    """
    Execute web search and return formatted results.
    
    Returns:
        Formatted search results string, or None if search fails
    """
    if not tavily_client:
        logger.warning("Tavily client not configured")
        return None
    
    if not query or len(query.strip()) < 3:
        return None
    
    try:
        logger.info(f"Executing web search: {query}")
        
        response = tavily_client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            include_answer=True,
        )
        
        if not response:
            return None
        
        results = []
        
        # Include Tavily's AI-generated answer if available
        if response.get("answer"):
            results.append(f"**Summary:** {response['answer']}\n")
        
        # Format individual results
        search_results = response.get("results", [])
        if not search_results:
            return None
        
        for i, item in enumerate(search_results, 1):
            title = item.get("title", "Untitled")
            url = item.get("url", "")
            content = item.get("content", "")
            
            # Clean and truncate content
            content = content.strip()[:500] if content else ""
            
            result_text = f"**Source {i}:** {title}"
            if url:
                result_text += f"\nURL: {url}"
            if content:
                result_text += f"\n{content}"
            
            results.append(result_text)
        
        return "\n\n---\n\n".join(results)
        
    except Exception as e:
        logger.error(f"Web search error: {e}")
        return None


def summarize_history_text(
    history_text: str,
    existing_summary: Optional[str] = None
) -> str:
    """
    Summarize conversation history to maintain context without repetition.
    """
    if not history_text or len(history_text.strip()) < 50:
        return existing_summary or ""
    
    try:
        llm = get_llm()
        
        if existing_summary:
            prompt = f"""Update this conversation summary with new information.

EXISTING SUMMARY:
{existing_summary}

NEW MESSAGES TO INCORPORATE:
{history_text}

INSTRUCTIONS:
- Keep the summary concise (2-3 paragraphs max)
- Focus on key facts, user preferences, and important context
- Remove redundant information
- Don't repeat the same facts multiple times

Updated summary:"""
        else:
            prompt = f"""Create a concise summary of this conversation.

CONVERSATION:
{history_text}

INSTRUCTIONS:
- Keep it to 2-3 paragraphs max
- Focus on key facts, decisions, and user preferences
- Capture important context for future responses

Summary:"""
        
        result = llm.invoke([
            SystemMessage(content=SUMMARY_SYSTEM_PROMPT),
            HumanMessage(content=prompt)
        ])
        
        return (result.content or "").strip()
        
    except Exception as e:
        logger.error(f"Summary generation error: {e}")
        return existing_summary or ""


def get_current_datetime() -> str:
    """Get current date and time in a readable format."""
    now = datetime.now(timezone.utc)
    return now.strftime("%A, %B %d, %Y at %H:%M UTC")
