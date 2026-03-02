# agent/main.py
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from pydantic import BaseModel

from .models import (
    BookingContext,
    ConciergeAsk,
    ConciergeChatRequest,
    ConciergeChatResponse,
    ConciergeResponse,
    Preferences,
)
from .db import (
    append_chat_message,
    ensure_chat_table,
    get_booking_with_user,
    get_chat_history,
    get_traveler_prefs,
)
from .providers.weather import geocode_location, get_weather_daily
from .planner import generate_concierge
from .chat_agent import run_concierge_chat, run_concierge_chat_stream

load_dotenv()

app = FastAPI(title="AI Concierge Agent")

# allow your React origin(s)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000","http://127.0.0.1:3000"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

def _coerce_date(value: Any) -> date:
    if value is None:
        raise ValueError("date value required")
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("invalid date format") from exc


async def _build_concierge_response(ask: ConciergeAsk) -> ConciergeResponse:
    # 1) if booking_id present, hydrate from DB
    booking = ask.booking
    if booking.booking_id:
        row = get_booking_with_user(booking.booking_id)
        if not row: raise HTTPException(404, "Booking not found")
        booking.location = row.get("location") or booking.location
        booking.address = booking.address or row.get("property_address") or row.get("location")
        booking.guests = booking.guests or row.get("guests")
        booking.party_type = booking.party_type or row.get("party_type")

    if booking.address and not booking.location:
        booking.location = booking.address

    if not booking.location:
        raise HTTPException(400, "location required (in booking or via booking_id)")

    target_location = booking.address or booking.location
    if target_location and (booking.lat is None or booking.lon is None):
        coords = await geocode_location(target_location)
        if coords:
            booking.lat, booking.lon = coords

    # 2) Fetch weather when coordinates are available
    weather = None
    if booking.lat is not None and booking.lon is not None:
        try:
            weather = await get_weather_daily(booking.lat, booking.lon)
        except Exception:
            weather = None  # don't fail the whole request

    # 3) if prefs not provided, try DB
    if ask.prefs is None and booking.booking_id:
        br = get_booking_with_user(booking.booking_id)
        if br:
            prefs = get_traveler_prefs(br["traveler_id"])
            if prefs:
                ask.prefs = Preferences(**prefs)

    # 4) generate
    return await generate_concierge(ask, weather_daily=weather)


@app.post("/ai/concierge", response_model=ConciergeResponse)
async def concierge(ask: ConciergeAsk):
    return await _build_concierge_response(ask)


@app.post("/ai/concierge/chat", response_model=ConciergeChatResponse)
async def concierge_chat(req: ConciergeChatRequest):
    context = dict(req.context or {})

    active_booking = context.get("active_booking")
    active_location = None
    if isinstance(active_booking, dict):
        active_location = (
            active_booking.get("location")
            or active_booking.get("property_location")
            or active_booking.get("address")
        )
        if active_location and active_booking.get("location") != active_location:
            active_booking = dict(active_booking)
            active_booking["location"] = active_location
            context["active_booking"] = active_booking
        elif active_booking is not None:
            context["active_booking"] = active_booking

    bookings_ctx = context.get("bookings")
    if isinstance(bookings_ctx, list):
        updated = []
        mutated = False
        for entry in bookings_ctx:
            if isinstance(entry, dict):
                ctx_loc = (
                    entry.get("location")
                    or entry.get("property_location")
                    or entry.get("address")
                )
                if ctx_loc and entry.get("location") != ctx_loc:
                    entry = dict(entry)
                    entry["location"] = ctx_loc
                    mutated = True
                updated.append(entry)
            else:
                updated.append(entry)
        if mutated:
            context["bookings"] = updated

    if active_location:
        context["active_booking_location"] = active_location

    thread_id = str(
        context.get("booking_id")
        or (active_booking.get("booking_id") if isinstance(active_booking, dict) else None)
        or "default"
    )
    reply = await run_concierge_chat(
        [msg.model_dump() for msg in req.messages],
        context,
        thread_id=thread_id,
    )
    return ConciergeChatResponse(reply=reply)


@app.post("/ai/concierge/chat/stream")
async def concierge_chat_stream(req: ConciergeChatRequest):
    """
    SSE endpoint that streams Gemini tokens back to the client as they are
    generated by the LangGraph agent.

    Response format (text/event-stream):
      data: {"token": "<text chunk>"}
      ...
      data: [DONE]
    """
    context = dict(req.context or {})

    active_booking = context.get("active_booking")
    active_location = None
    if isinstance(active_booking, dict):
        active_location = (
            active_booking.get("location")
            or active_booking.get("property_location")
            or active_booking.get("address")
        )
        if active_location and active_booking.get("location") != active_location:
            active_booking = dict(active_booking)
            active_booking["location"] = active_location
            context["active_booking"] = active_booking
        elif active_booking is not None:
            context["active_booking"] = active_booking

    if active_location:
        context["active_booking_location"] = active_location

    thread_id = str(
        context.get("booking_id")
        or (active_booking.get("booking_id") if isinstance(active_booking, dict) else None)
        or "default"
    )

    async def generate():
        async for token in run_concierge_chat_stream(
            [msg.model_dump() for msg in req.messages],
            context,
            thread_id=thread_id,
        ):
            import json as _json
            yield f"data: {_json.dumps({'token': token})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


class LegacyChatRequest(BaseModel):
    booking: Optional[Dict[str, Any]] = None
    message: str
    prefs: Optional[Dict[str, Any]] = None
    history: Optional[List[Dict[str, Any]]] = None


@app.post("/ai/chat")
async def concierge_chat_legacy(payload: LegacyChatRequest):
    if not payload.booking:
        raise HTTPException(400, "booking is required")

    booking_raw = payload.booking
    start_raw = booking_raw.get("start_date") or booking_raw.get("startDate")
    end_raw = booking_raw.get("end_date") or booking_raw.get("endDate")
    if not start_raw or not end_raw:
        raise HTTPException(400, "start_date and end_date are required")

    try:
        booking = BookingContext(
            booking_id=booking_raw.get("booking_id") or booking_raw.get("id"),
            location=booking_raw.get("location") or booking_raw.get("property_location"),
            address=booking_raw.get("address") or booking_raw.get("property_address"),
            lat=booking_raw.get("lat"),
            lon=booking_raw.get("lon"),
            start_date=_coerce_date(start_raw),
            end_date=_coerce_date(end_raw),
            party_type=booking_raw.get("party_type") or booking_raw.get("partyType"),
            guests=booking_raw.get("guests"),
        )
    except Exception as exc:
        raise HTTPException(400, f"invalid booking payload: {exc}") from exc

    prefs_model: Optional[Preferences] = None
    if payload.prefs:
        try:
            prefs_model = Preferences(**payload.prefs)
        except Exception as exc:
            raise HTTPException(400, f"invalid prefs payload: {exc}") from exc

    ask = ConciergeAsk(booking=booking, prefs=prefs_model, free_text=payload.message)
    concierge = await _build_concierge_response(ask)
    if ask.booking.location:
        booking_raw["location"] = ask.booking.location
        booking_raw["property_location"] = ask.booking.location
    if ask.booking.address:
        booking_raw["address"] = ask.booking.address
        booking_raw["property_address"] = ask.booking.address

    # Persist conversation if DB is available
    ensure_chat_table()
    if booking.booking_id:
        append_chat_message(booking.booking_id, "user", payload.message)

    history_messages = payload.history or []
    chat_messages = []
    for item in history_messages:
        role = item.get("role")
        content = item.get("content") or item.get("text")
        if role in ("user", "assistant") and content:
            chat_messages.append({"role": role, "content": content})
    chat_messages.append({"role": "user", "content": payload.message})

    try:
        reply = await run_concierge_chat(chat_messages, {"active_booking": booking_raw})
    except Exception:
        reply = "Here is your updated concierge plan."

    if booking.booking_id:
        append_chat_message(booking.booking_id, "assistant", reply)

    return {
        "reply": reply,
        "concierge": concierge.model_dump(mode="json"),
    }


@app.get("/ai/health")
async def health():
    from .providers import llm as llmprov
    return {
        "ok": True,
        "model": getattr(llmprov, "MODEL_NAME", None),
        "gemini_key_present": bool(os.getenv("GEMINI_API_KEY")),
        "tavily_key_present": bool(os.getenv("TAVILY_API_KEY")),
        "openweather_key_present": bool(os.getenv("OPENWEATHER_API_KEY")),
    }


@app.get("/ai/history")
async def history(booking_id: Optional[int] = None, limit: int = 200):
    if not booking_id:
        return {"history": []}
    ensure_chat_table()
    rows = get_chat_history(booking_id, limit=limit)
    return {"history": rows}
