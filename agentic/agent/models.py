# agent/models.py
from typing import List, Optional, Literal, Dict
from pydantic import BaseModel, Field
from datetime import date

class BookingContext(BaseModel):
    booking_id: Optional[int] = None
    location: Optional[str] = None
    address: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    start_date: date
    end_date: date
    party_type: Optional[str] = None
    guests: Optional[int] = None

class Preferences(BaseModel):
    budget: Literal["low","mid","high"] = "mid"
    interests: List[str] = Field(default_factory=list)
    mobility_needs: Literal["none","wheelchair","limited_walk"] = "none"
    dietary: Literal["none","vegan","vegetarian","halal","kosher","gluten_free"] = "none"

class ConciergeAsk(BaseModel):
    booking: BookingContext
    prefs: Optional[Preferences] = None
    free_text: Optional[str] = None  # NLU

# -------- response -----------
class ActivityCard(BaseModel):
    title: str
    address: Optional[str] = None
    geo: Optional[tuple[float,float]] = None
    price_tier: Optional[Literal["$","$$","$$$"]] = None
    duration_min: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    wheelchair_friendly: Optional[bool] = None
    child_friendly: Optional[bool] = None
    url: Optional[str] = None

class DayPlan(BaseModel):
    date: date
    morning: List[ActivityCard] = Field(default_factory=list)
    afternoon: List[ActivityCard] = Field(default_factory=list)
    evening: List[ActivityCard] = Field(default_factory=list)

class ConciergeResponse(BaseModel):
    plan: List[DayPlan]
    restaurants: List[ActivityCard]
    packing_checklist: List[str]
    reasoning_notes: List[str] = Field(default_factory=list)  # optional, for debugging


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ConciergeChatRequest(BaseModel):
    messages: List[ChatMessage]
    context: Dict = Field(default_factory=dict)


class ConciergeChatResponse(BaseModel):
    reply: str
