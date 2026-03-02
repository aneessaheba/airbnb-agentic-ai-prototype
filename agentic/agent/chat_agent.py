"""
chat_agent.py — LangGraph-based AI concierge agent.

Architecture:
  AgentState (messages + context)
       │
  ┌────▼──────┐    tool_calls?    ┌────────────┐
  │call_model │ ─── yes ────────► │ call_tools │
  └────┬──────┘                   └─────┬──────┘
       │ no (END)                       │ loop back
       ▼                                ▼
     reply                         call_model

Memory is managed by LangGraph's MemorySaver, keyed per thread_id
(typically the booking_id), giving the agent persistent conversational
context across turns in the same session.
"""

import json
import os
import re
from datetime import datetime
from typing import Annotated, Any, Dict, List, Optional, Sequence
from typing import TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
    add_messages,
)
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .providers.weather import geocode_location, get_weather_daily
from .providers.search import search_pois


# ─── Tool Definitions ──────────────────────────────────────────────────────────

@tool
async def get_weather_forecast(location: str) -> str:
    """Return a concise 5-day forecast (uses OpenWeather when available, otherwise Open-Meteo)."""
    if not location or not location.strip():
        return "Please provide a city, region, or recognizable location."

    coords = await geocode_location(location)
    if not coords:
        return (
            f"Unable to find coordinates for '{location}'. "
            "Try adding a state/country (example: 'Portland, OR, USA')."
        )

    lat, lon = coords
    try:
        data = await get_weather_daily(lat, lon)
    except Exception as exc:
        return f"Weather service error: {exc}"
    if not data:
        return f"Weather data is unavailable for '{location}' right now."

    daily = data.get("daily") or []
    if not daily:
        return f"No daily forecast entries returned for '{location}'."

    source = data.get("source")
    lines: List[str] = []
    for entry in daily[:5]:
        ts = entry.get("dt")
        if ts:
            day = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        else:
            day = "Unknown date"
        desc = (entry.get("weather") or [{}])[0].get("description") or "No description"
        temps = entry.get("temp") or {}
        max_c = temps.get("max")
        min_c = temps.get("min")
        pop = entry.get("pop")

        segment = f"{day}: {desc}"
        if max_c is not None or min_c is not None:
            if max_c is not None and min_c is not None:
                segment += f", high {round(float(max_c), 1)}°C / low {round(float(min_c), 1)}°C"
            elif max_c is not None:
                segment += f", high {round(float(max_c), 1)}°C"
            elif min_c is not None:
                segment += f", low {round(float(min_c), 1)}°C"

        if pop is not None:
            try:
                pop_pct = int(round(float(pop) * 100))
                segment += f", precip {pop_pct}%"
            except (TypeError, ValueError):
                pass

        lines.append(segment)

    header = f"5-day forecast for {location.strip()}"
    if source:
        header += f" (source: {source})"
    header += ":"
    return header + "\n" + "\n".join(lines)


CHAT_TOOLS = [get_weather_forecast]
TOOL_MAP = {t.name: t for t in CHAT_TOOLS}


# ─── LLM ──────────────────────────────────────────────────────────────────────

_llm: Optional[ChatGoogleGenerativeAI] = None


def _get_llm() -> Optional[ChatGoogleGenerativeAI]:
    """Create (and cache) the Gemini chat model if the key is present."""
    global _llm
    if _llm is not None:
        return _llm

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        _llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            temperature=0.4,
            google_api_key=api_key,
            convert_system_message_to_human=False,
        )
    except Exception:
        _llm = None
    return _llm


# ─── LangGraph Agent State ─────────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    context: Dict[str, Any]


# ─── Graph Nodes ───────────────────────────────────────────────────────────────

async def _node_call_model(state: AgentState) -> Dict:
    """Invoke the LLM with bound tools. Returns the model response as a new message."""
    llm = _get_llm()
    if llm is None:
        return {
            "messages": [
                AIMessage(
                    content=(
                        "I'm ready to help plan your trip once a Gemini API key is configured. "
                        "In the meantime, you can use the standard concierge plan generator."
                    )
                )
            ]
        }
    llm_with_tools = llm.bind_tools(CHAT_TOOLS)
    response: AIMessage = await llm_with_tools.ainvoke(state["messages"])
    return {"messages": [response]}


async def _node_call_tools(state: AgentState) -> Dict:
    """Execute any tool calls requested by the model and return ToolMessages."""
    last = state["messages"][-1]
    tool_messages: List[ToolMessage] = []

    for call in getattr(last, "tool_calls", []):
        if isinstance(call, dict):
            call_id = call.get("id")
            call_name = call.get("name")
            raw_args = call.get("args") or call.get("function", {}).get("arguments")
        else:
            call_id = getattr(call, "id", None)
            call_name = getattr(call, "name", None)
            raw_args = getattr(call, "args", None)
            if raw_args is None:
                raw_args = getattr(getattr(call, "function", None), "arguments", None)

        args: Dict[str, Any]
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        tool_obj = TOOL_MAP.get(call_name)
        if tool_obj is None:
            tool_output = f"Tool '{call_name}' is not available."
        else:
            try:
                if hasattr(tool_obj, "arun"):
                    tool_output = await tool_obj.arun(**args)
                else:
                    tool_output = tool_obj.run(**args)
            except Exception as exc:
                tool_output = f"Tool error: {exc}"

        tool_messages.append(
            ToolMessage(
                content=str(tool_output),
                tool_call_id=call_id or call_name or "unknown_tool_call",
            )
        )

    return {"messages": tool_messages}


def _should_continue(state: AgentState) -> str:
    """Route to tool execution if tool calls are present, otherwise end the graph."""
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "call_tools"
    return END


# ─── Compile LangGraph Workflow ────────────────────────────────────────────────

_memory = MemorySaver()

_workflow = StateGraph(AgentState)
_workflow.add_node("call_model", _node_call_model)
_workflow.add_node("call_tools", _node_call_tools)
_workflow.set_entry_point("call_model")
_workflow.add_conditional_edges("call_model", _should_continue)
_workflow.add_edge("call_tools", "call_model")

agent_graph = _workflow.compile(checkpointer=_memory)


# ─── Message Helpers ───────────────────────────────────────────────────────────

def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(item.get("text") or "")
            else:
                parts.append(getattr(item, "text", str(item)))
        return "\n".join(part for part in parts if part)
    return str(content)


def _build_messages(
    messages: List[Dict[str, Any]], context: Dict[str, Any]
) -> List[BaseMessage]:
    """Convert raw message dicts + context into LangChain message objects."""
    context_str = json.dumps(context or {}, default=str)
    base_prompt = (
        "You are an AI travel concierge helping Airbnb guests. "
        "Blend warm hospitality with concrete suggestions. "
        "Always leverage any booking details, traveler preferences, weather, favorite properties, "
        "and search highlights present in the provided context JSON. "
        "If you propose an itinerary, break it into clear sections (Day 1, Morning/Afternoon/Evening, etc.). "
        "Offer practical packing or local tips when relevant.\n"
        f"Context JSON: {context_str}"
    )

    conversation: List[BaseMessage] = [SystemMessage(content=base_prompt)]
    for msg in messages:
        text = msg.get("content") or ""
        if not text:
            continue
        if msg.get("role") == "user":
            conversation.append(HumanMessage(content=text))
        else:
            conversation.append(AIMessage(content=text))
    return conversation


async def _prefetch_context(
    conversation: List[BaseMessage],
    context: Dict[str, Any],
    last_user_text: Optional[str],
) -> List[BaseMessage]:
    """
    Pre-fetch weather and dining context based on keywords in the latest user
    message, injecting results as SystemMessages before the graph is invoked.
    This reduces tool round-trips for common travel queries.
    """
    weather_keywords = ("weather", "forecast", "rain", "temperature", "climate")
    dining_keywords = ("restaurant", "dining", "dinner", "breakfast", "lunch", "food", "eatery", "cuisine")

    active_booking = context.get("active_booking") if isinstance(context, dict) else None
    booking_start = booking_end = None
    if isinstance(active_booking, dict):
        booking_start = active_booking.get("start_date") or active_booking.get("startDate")
        booking_end = active_booking.get("end_date") or active_booking.get("endDate")

    location_hint = (
        context.get("active_booking_location")
        or (active_booking.get("location") if isinstance(active_booking, dict) else None)
    )
    if not location_hint and last_user_text:
        match = re.search(r"(?:for|in|at)\s+([A-Za-z][A-Za-z\s',-]+)", last_user_text, re.IGNORECASE)
        if match:
            location_hint = match.group(1).strip(" .,!?:;")

    lowered = last_user_text.lower() if last_user_text else ""

    if location_hint and lowered:
        if any(word in lowered for word in weather_keywords):
            try:
                weather_summary = await get_weather_forecast.arun(location_hint)
                date_context = ""
                if booking_start and booking_end:
                    date_context = f" (stay {booking_start} to {booking_end})"
                conversation.append(
                    SystemMessage(
                        content=f"Automated weather fetch for {location_hint}{date_context}:\n{weather_summary}"
                    )
                )
            except Exception as exc:
                conversation.append(
                    SystemMessage(content=f"Automated weather fetch for {location_hint} failed: {exc}")
                )

        if any(word in lowered for word in dining_keywords):
            try:
                queries = [
                    "best restaurants",
                    "top local dining",
                    "family friendly restaurants" if "kid" in lowered else "popular eateries",
                    last_user_text,
                ]
                hits = search_pois(location_hint, queries, max_results=6)
                if hits:
                    top_hits = []
                    seen_titles = set()
                    for hit in hits:
                        title = (hit.get("title") or "").strip()
                        if not title or title in seen_titles:
                            continue
                        seen_titles.add(title)
                        url = hit.get("url") or ""
                        top_hits.append(f"- {title}" + (f" ({url})" if url else ""))
                        if len(top_hits) >= 5:
                            break
                    if top_hits:
                        conversation.append(
                            SystemMessage(
                                content=(
                                    f"Nearby dining suggestions for {location_hint}:\n"
                                    + "\n".join(top_hits)
                                )
                            )
                        )
            except Exception as exc:
                conversation.append(
                    SystemMessage(content=f"Automated dining lookup for {location_hint} failed: {exc}")
                )

    return conversation


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_concierge_chat(
    messages: List[Dict[str, Any]],
    context: Dict[str, Any],
    thread_id: str = "default",
) -> str:
    """
    Run the LangGraph concierge agent and return the final reply text.

    The graph executes: preprocess → call_model → (call_tools → call_model)*
    MemorySaver persists conversation state per thread_id so the agent
    retains context across multiple turns within the same session.
    """
    if not messages:
        return "Hello! Tell me about your upcoming trip and I can help plan it."

    last_user_text: Optional[str] = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_text = msg.get("content") or ""
            break

    conversation = _build_messages(messages, context)
    conversation = await _prefetch_context(conversation, context, last_user_text)

    config = {"configurable": {"thread_id": thread_id}}
    state: AgentState = {"messages": conversation, "context": context}

    try:
        result = await agent_graph.ainvoke(state, config=config)
        final_message = result["messages"][-1]
        return _message_text(final_message)
    except Exception:
        return (
            "I couldn't reach the language model just now, but you can still generate a plan "
            "from the main concierge button and try again shortly."
        )


async def run_concierge_chat_stream(
    messages: List[Dict[str, Any]],
    context: Dict[str, Any],
    thread_id: str = "default",
):
    """
    Stream token-by-token output from the LangGraph concierge agent.

    Uses astream_events (v2) to capture on_chat_model_stream events and
    yield individual text tokens for SSE delivery to the frontend.
    """
    if not messages:
        yield "Hello! Tell me about your upcoming trip and I can help plan it."
        return

    last_user_text: Optional[str] = None
    for msg in reversed(messages):
        if msg.get("role") == "user":
            last_user_text = msg.get("content") or ""
            break

    conversation = _build_messages(messages, context)
    conversation = await _prefetch_context(conversation, context, last_user_text)

    config = {"configurable": {"thread_id": thread_id}}
    state: AgentState = {"messages": conversation, "context": context}

    try:
        async for event in agent_graph.astream_events(state, config=config, version="v2"):
            if event["event"] != "on_chat_model_stream":
                continue
            chunk = event["data"].get("chunk")
            if chunk is None:
                continue
            content = getattr(chunk, "content", None)
            if not content:
                continue
            if isinstance(content, str):
                yield content
            elif isinstance(content, list):
                for part in content:
                    text = part.get("text") if isinstance(part, dict) else getattr(part, "text", "")
                    if text:
                        yield text
    except Exception:
        yield (
            "I couldn't reach the language model just now. "
            "Please try again or use the plan generator."
        )
