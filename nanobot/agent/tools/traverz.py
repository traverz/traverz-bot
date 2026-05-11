"""Traverz backend tools — read and modify trip data via the Traverz REST API.

All tools read the current user's JWT and trip_id from the per-turn context vars
set by the WebSocket/WhatsApp channel.  Write operations additionally check that
the user has owner or editor role.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from loguru import logger

from nanobot.agent.tools.base import Tool, tool_parameters
from nanobot.agent.tools.schema import (
    ArraySchema,
    BooleanSchema,
    NumberSchema,
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.traverz import context as _ctx
from nanobot.utils.exceptions import AuthExpiredError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKEND_URL = os.environ.get("TRAVERZ_BACKEND_URL", "https://api.traverz.ai")
_TIMEOUT = 20  # seconds

# Shared async client — reuses TCP+TLS connections across tool calls.
# Created lazily on first use so tests can patch _BACKEND_URL before it is built.
_http_client: httpx.AsyncClient | None = None


def _client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _http_client


def _headers() -> dict[str, str]:
    """Build auth headers from the current-turn context var."""
    jwt = _ctx.user_jwt.get()
    if not jwt:
        raise PermissionError("No user JWT available — cannot call Traverz backend.")
    return {
        "Authorization": f"Bearer {jwt}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _require_trip_id() -> str:
    tid = _ctx.trip_id.get()
    if not tid:
        raise ValueError("No trip context — the session is not scoped to a trip.")
    return tid


def _require_write() -> None:
    role = _ctx.user_role.get()
    if role not in ("owner", "editor"):
        raise PermissionError(
            f"You have '{role}' access to this trip.  Only owners and editors can make changes."
        )


def _check_auth(resp: httpx.Response) -> None:
    """Raise AuthExpiredError immediately on HTTP 401 so the agent loop aborts."""
    if resp.status_code == 401:
        raise AuthExpiredError()


async def _get(path: str, params: dict | None = None) -> Any:
    url = f"{_BACKEND_URL}{path}"
    resp = await _client().get(url, headers=_headers(), params=params)
    _check_auth(resp)
    resp.raise_for_status()
    return resp.json()


async def _post(path: str, body: dict) -> Any:
    url = f"{_BACKEND_URL}{path}"
    resp = await _client().post(url, headers=_headers(), content=json.dumps(body))
    _check_auth(resp)
    resp.raise_for_status()
    return resp.json()


async def _patch(path: str, body: dict) -> Any:
    url = f"{_BACKEND_URL}{path}"
    resp = await _client().patch(url, headers=_headers(), content=json.dumps(body))
    _check_auth(resp)
    resp.raise_for_status()
    return resp.json()


async def _delete(path: str) -> str:
    url = f"{_BACKEND_URL}{path}"
    resp = await _client().delete(url, headers=_headers())
    _check_auth(resp)
    resp.raise_for_status()
    return "Deleted."


def _fmt(obj: Any, max_chars: int = 6000) -> str:
    text = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... (truncated)"
    return text


# ---------------------------------------------------------------------------
# Trip read/write tools
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(required=[])
)
class GetTripTool(Tool):
    """Fetch the current trip's full details (title, dates, cities, members, status).

    Always call this first so you have the authoritative trip data.
    """

    @property
    def name(self) -> str:
        return "get_trip"

    @property
    def description(self) -> str:
        return (
            "Fetch the current trip's details including title, destination cities, "
            "dates, status, members and form_data.  "
            "Always call this before trying to read or edit the trip."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_: Any) -> str:
        trip_id = _require_trip_id()
        data = await _get(f"/api/trips/{trip_id}/")
        # Resolve the authoritative role from the backend response so that the
        # client-supplied user_role (which defaults to "viewer") never masks a
        # real owner or editor.
        if isinstance(data, dict):
            if data.get("is_owner"):
                _ctx.user_role.set("owner")
            elif data.get("can_edit"):
                _ctx.user_role.set("editor")
            # if neither flag is set, leave the existing role (viewer by default)
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        title=StringSchema("New trip title", nullable=True),
        description=StringSchema("New trip description", nullable=True),
        start_date=StringSchema("Start date YYYY-MM-DD", nullable=True),
        end_date=StringSchema("End date YYYY-MM-DD", nullable=True),
        timezone=StringSchema("IANA timezone e.g. Asia/Singapore", nullable=True),
        status=StringSchema(
            "Trip status",
            enum=["draft", "brainstorming", "planning_ready", "planned", "upcoming", "active", "completed", "archived"],
            nullable=True,
        ),
        required=[],
    )
)
class UpdateTripTool(Tool):
    """Update the current trip's metadata (title, dates, timezone, status, etc.)."""

    @property
    def name(self) -> str:
        return "update_trip"

    @property
    def description(self) -> str:
        return (
            "Update the current trip's title, description, start/end dates, timezone or status.  "
            "Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            title=StringSchema("New trip title", nullable=True),
            description=StringSchema("New trip description", nullable=True),
            start_date=StringSchema("Start date YYYY-MM-DD", nullable=True),
            end_date=StringSchema("End date YYYY-MM-DD", nullable=True),
            timezone=StringSchema("IANA timezone e.g. Asia/Singapore", nullable=True),
            status=StringSchema(
                "Trip status",
                enum=["draft", "brainstorming", "planning_ready", "planned", "upcoming", "active", "completed", "archived"],
                nullable=True,
            ),
            required=[],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()
        body = {k: v for k, v in kwargs.items() if v is not None}
        if not body:
            return "Nothing to update."
        data = await _patch(f"/api/trips/{trip_id}/update/", body)
        return _fmt(data)


# ---------------------------------------------------------------------------
# Events / itinerary
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(required=[])
)
class GetItineraryTool(Tool):
    """Fetch all events/itinerary items for the current trip, ordered by date."""

    @property
    def name(self) -> str:
        return "get_itinerary"

    @property
    def description(self) -> str:
        return (
            "List all itinerary events for the current trip sorted chronologically.  "
            "Returns type, title, datetime, location and cost for each event."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_: Any) -> str:
        trip_id = _require_trip_id()
        data = await _get(f"/api/trips/{trip_id}/")
        events = data.get("events", [])
        # Sort by start_datetime for readability
        events.sort(key=lambda e: e.get("start_datetime") or "")
        compact = [
            {
                "id": e.get("id"),
                "type": e.get("type"),
                "subtype": e.get("subtype"),
                "title": e.get("title"),
                "start_datetime": e.get("start_datetime"),
                "end_datetime": e.get("end_datetime"),
                "location_address": e.get("location_address"),
                "cost": e.get("cost"),
                "currency": e.get("currency"),
                "notes": e.get("notes"),
            }
            for e in events
        ]
        return _fmt(compact)


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema("Attraction name to search for"),
        city_name=StringSchema("City name (optional — helps narrow results)", nullable=True),
        required=["name"],
    )
)
class SearchAttractionTool(Tool):
    """Look up an attraction in the Traverz database to get its image, coordinates and Google Place ID."""

    @property
    def name(self) -> str:
        return "search_attraction"

    @property
    def description(self) -> str:
        return (
            "Search the Traverz database for an attraction by name and optional city. "
            "Returns image_url, latitude, longitude, place_id and address so you can "
            "enrich add_event calls with photo and map data."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            name=StringSchema("Attraction name to search for"),
            city_name=StringSchema("City name (optional — helps narrow results)", nullable=True),
            required=["name"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, name: str, city_name: str | None = None, **_: Any) -> str:
        params: dict[str, str] = {"search": name}
        if city_name:
            params["city_name"] = city_name
        data = await _get("/api/cities/attractions/", params=params)
        results = data if isinstance(data, list) else data.get("results", [])
        if not results:
            return f"No attraction found matching '{name}'" + (f" in {city_name}" if city_name else "")
        # Return the top 3 matches with the fields needed for add_event
        compact = []
        for r in results[:3]:
            place_id = r.get("place_id") or ""
            google_map_uri = (
                f"https://www.google.com/maps/place/?q=place_id:{place_id}"
                if place_id
                else f"https://www.google.com/maps/search/?api=1&query={name.replace(' ', '+')}"
                + (f"+{city_name.replace(' ', '+')}" if city_name else "")
            )
            compact.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "city": r.get("city_name"),
                "image_url": r.get("primary_image") or r.get("image_url") or "",
                "latitude": r.get("latitude"),
                "longitude": r.get("longitude"),
                "place_id": place_id,
                "google_map_uri": google_map_uri,
                "address": r.get("vicinity") or r.get("address") or "",
                "rating": r.get("rating"),
            })
        return _fmt(compact)


@tool_parameters(
    tool_parameters_schema(
        type=StringSchema(
            "Event type: destination | accommodation | activity | transport | meal | note",
            enum=["destination", "accommodation", "activity", "transport", "meal", "note"],
        ),
        title=StringSchema("Event title"),
        start_datetime=StringSchema("ISO 8601 datetime e.g. 2025-06-15T09:00:00"),
        end_datetime=StringSchema("ISO 8601 datetime (optional)", nullable=True),
        location_address=StringSchema("Address or place name (optional)", nullable=True),
        google_map_uri=StringSchema("Google Maps URI for the place (optional)", nullable=True),
        image_url=StringSchema("Photo/cover image URL for the event (optional)", nullable=True),
        location_lat=NumberSchema(description="Latitude (optional)", nullable=True),
        location_lng=NumberSchema(description="Longitude (optional)", nullable=True),
        location_place_id=StringSchema("Google Place ID (optional)", nullable=True),
        cost=NumberSchema(description="Cost amount (optional)", nullable=True),
        currency=StringSchema("3-letter currency code e.g. SGD (optional)", nullable=True),
        notes=StringSchema("Extra notes (optional)", nullable=True),
        description=StringSchema("Longer event description (optional)", nullable=True),
        subtype=StringSchema(
            "Transport subtype: flight | train | bus | car | ferry | other (optional)",
            enum=["flight", "train", "bus", "car", "ferry", "other"],
            nullable=True,
        ),
        required=["type", "title", "start_datetime"],
    )
)
class AddEventTool(Tool):
    """Add a new event to the current trip's itinerary."""

    @property
    def name(self) -> str:
        return "add_event"

    @property
    def description(self) -> str:
        return (
            "Add a new event (activity, accommodation, transport, meal, note, destination) "
            "to the current trip's itinerary.  Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            type=StringSchema(
                "Event type",
                enum=["destination", "accommodation", "activity", "transport", "meal", "note"],
            ),
            title=StringSchema("Event title"),
            start_datetime=StringSchema("ISO 8601 datetime e.g. 2025-06-15T09:00:00"),
            end_datetime=StringSchema("ISO 8601 datetime (optional)", nullable=True),
            location_address=StringSchema("Address or place name (optional)", nullable=True),
            google_map_uri=StringSchema("Google Maps URI for the place (optional)", nullable=True),
            image_url=StringSchema("Photo/cover image URL for the event (optional)", nullable=True),
            location_lat=NumberSchema(description="Latitude (optional)", nullable=True),
            location_lng=NumberSchema(description="Longitude (optional)", nullable=True),
            location_place_id=StringSchema("Google Place ID (optional)", nullable=True),
            cost=NumberSchema(description="Cost amount (optional)", nullable=True),
            currency=StringSchema("3-letter currency code (optional)", nullable=True),
            notes=StringSchema("Extra notes (optional)", nullable=True),
            description=StringSchema("Longer event description (optional)", nullable=True),
            subtype=StringSchema(
                "Transport subtype",
                enum=["flight", "train", "bus", "car", "ferry", "other"],
                nullable=True,
            ),
            required=["type", "title", "start_datetime"],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()
        body = {k: v for k, v in kwargs.items() if v is not None}
        data = await _post(f"/api/trips/{trip_id}/events/", body)
        return _fmt(data)


_EVENT_ITEM_SCHEMA = ObjectSchema(
    type=StringSchema(
        "Event type",
        enum=["destination", "accommodation", "activity", "transport", "meal", "note"],
    ),
    title=StringSchema("Event title"),
    start_datetime=StringSchema("ISO 8601 datetime e.g. 2025-06-15T09:00:00"),
    end_datetime=StringSchema("ISO 8601 end datetime (optional)", nullable=True),
    location_address=StringSchema("Address or place name (optional)", nullable=True),
    description=StringSchema("Longer event description (optional)", nullable=True),
    notes=StringSchema("Extra notes (optional)", nullable=True),
    cost=NumberSchema(description="Cost amount (optional)", nullable=True),
    currency=StringSchema("3-letter currency code e.g. SGD (optional)", nullable=True),
    subtype=StringSchema(
        "Transport subtype: flight | train | bus | car | ferry | other (optional)",
        enum=["flight", "train", "bus", "car", "ferry", "other"],
        nullable=True,
    ),
    image_url=StringSchema(
        "Public image URL for the place or event (optional). Use a known official photo URL.",
        nullable=True,
    ),
    google_map_uri=StringSchema(
        "Google Maps URL for the location, e.g. https://maps.google.com/?cid=... (optional)",
        nullable=True,
    ),
    required=["type", "title", "start_datetime"],
)


@tool_parameters(
    tool_parameters_schema(
        events=ArraySchema(
            items=_EVENT_ITEM_SCHEMA,
            description="Array of event objects to create in one request",
            min_items=1,
        ),
        required=["events"],
    )
)
class BulkAddEventsTool(Tool):
    """Add multiple events to the current trip's itinerary in a single request.

    Prefer this over calling add_event repeatedly whenever you have 2 or more
    events to create at once — it is significantly faster and atomic.
    """

    @property
    def name(self) -> str:
        return "bulk_add_events"

    @property
    def description(self) -> str:
        return (
            "Add multiple itinerary events to the current trip in one request.  "
            "Always use this instead of calling add_event in a loop when you need "
            "to create 2 or more events.  Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            events=ArraySchema(
                items=_EVENT_ITEM_SCHEMA,
                description="Array of event objects to create",
                min_items=1,
            ),
            required=["events"],
        )

    async def execute(self, events: list[dict], **_: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()
        # Strip None values from each event object
        clean_events = [{k: v for k, v in ev.items() if v is not None} for ev in events]
        data = await _post(f"/api/trips/{trip_id}/events/bulk/", {"events": clean_events})
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        event_id=StringSchema("Event ID to update"),
        title=StringSchema("New title (optional)", nullable=True),
        start_datetime=StringSchema("New ISO 8601 start datetime (optional)", nullable=True),
        end_datetime=StringSchema("New ISO 8601 end datetime (optional)", nullable=True),
        location_address=StringSchema("New location address or place name (optional)", nullable=True),
        location_lat=NumberSchema(description="New latitude (optional)", nullable=True),
        location_lng=NumberSchema(description="New longitude (optional)", nullable=True),
        location_place_id=StringSchema("New Google Place ID (optional)", nullable=True),
        google_map_uri=StringSchema("New Google Maps URI (optional)", nullable=True),
        image_url=StringSchema("New cover image URL (optional)", nullable=True),
        cost=NumberSchema(description="New cost (optional)", nullable=True),
        currency=StringSchema("New currency code (optional)", nullable=True),
        notes=StringSchema("New notes (optional)", nullable=True),
        description=StringSchema("New description (optional)", nullable=True),
        required=["event_id"],
    )
)
class UpdateEventTool(Tool):
    """Update an existing event in the current trip."""

    @property
    def name(self) -> str:
        return "update_event"

    @property
    def description(self) -> str:
        return (
            "Update fields of an existing itinerary event.  "
            "Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            event_id=StringSchema("Event ID to update"),
            title=StringSchema("New title (optional)", nullable=True),
            start_datetime=StringSchema("New ISO 8601 start datetime (optional)", nullable=True),
            end_datetime=StringSchema("New ISO 8601 end datetime (optional)", nullable=True),
            location_address=StringSchema("New location address or place name (optional)", nullable=True),
            location_lat=NumberSchema(description="New latitude (optional)", nullable=True),
            location_lng=NumberSchema(description="New longitude (optional)", nullable=True),
            location_place_id=StringSchema("New Google Place ID (optional)", nullable=True),
            google_map_uri=StringSchema("New Google Maps URI (optional)", nullable=True),
            image_url=StringSchema("New cover image URL (optional)", nullable=True),
            cost=NumberSchema(description="New cost (optional)", nullable=True),
            currency=StringSchema("New currency code (optional)", nullable=True),
            notes=StringSchema("New notes (optional)", nullable=True),
            description=StringSchema("New description (optional)", nullable=True),
            required=["event_id"],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        event_id = kwargs.pop("event_id")
        body = {k: v for k, v in kwargs.items() if v is not None}
        if not body:
            return "Nothing to update."
        data = await _patch(f"/api/trips/events/{event_id}/", body)
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        event_id=StringSchema("Event ID to delete"),
        required=["event_id"],
    )
)
class DeleteEventTool(Tool):
    """Delete an event from the current trip."""

    @property
    def name(self) -> str:
        return "delete_event"

    @property
    def description(self) -> str:
        return (
            "Permanently delete an event from the current trip's itinerary.  "
            "Requires owner or editor role.  ALWAYS confirm with the user before deleting."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            event_id=StringSchema("Event ID to delete"),
            required=["event_id"],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        event_id = kwargs["event_id"]
        return await _delete(f"/api/trips/events/{event_id}/delete/")


# ---------------------------------------------------------------------------
# Budget & expenses
# ---------------------------------------------------------------------------


@tool_parameters(tool_parameters_schema(required=[]))
class GetBudgetTool(Tool):
    """Fetch the current trip's budget and expense summary."""

    @property
    def name(self) -> str:
        return "get_budget"

    @property
    def description(self) -> str:
        return (
            "Get the current trip's total budget, currency, spending summary by category "
            "and per-person balance (who owes whom)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_: Any) -> str:
        trip_id = _require_trip_id()
        summary = await _get(f"/api/budgeting/budgets/{trip_id}/summary/")
        balances = await _get(f"/api/budgeting/budgets/{trip_id}/balances/")
        return _fmt({"summary": summary, "balances": balances})


@tool_parameters(
    tool_parameters_schema(
        description=StringSchema("Short expense description e.g. 'Dinner at hawker centre'"),
        amount=NumberSchema(description="Amount (positive number)"),
        currency=StringSchema("3-letter ISO currency code e.g. SGD"),
        category=StringSchema(
            "Expense category (optional): accommodation | food | transport | activity | shopping | other",
            nullable=True,
        ),
        expense_date=StringSchema("Date YYYY-MM-DD (optional, defaults to today)", nullable=True),
        notes=StringSchema("Extra notes (optional)", nullable=True),
        is_personal=BooleanSchema(description="True if personal expense, False if shared (default False)", nullable=True),
        required=["description", "amount", "currency"],
    )
)
class AddExpenseTool(Tool):
    """Add an expense to the current trip's budget tracker."""

    @property
    def name(self) -> str:
        return "add_expense"

    @property
    def description(self) -> str:
        return (
            "Record a new expense against the current trip's budget.  "
            "Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            description=StringSchema("Short description"),
            amount=NumberSchema(description="Amount"),
            currency=StringSchema("3-letter ISO currency code"),
            category=StringSchema("Category", nullable=True),
            expense_date=StringSchema("Date YYYY-MM-DD", nullable=True),
            notes=StringSchema("Extra notes", nullable=True),
            is_personal=BooleanSchema(description="Personal expense flag", nullable=True),
            required=["description", "amount", "currency"],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()
        budget_info = await _get(f"/api/budgeting/budgets/{trip_id}/")
        budget_id = budget_info.get("id")
        body: dict[str, Any] = {
            "budget": budget_id,
            "description": kwargs["description"],
            "amount": kwargs["amount"],
            "currency": kwargs["currency"],
        }
        for opt in ("category", "expense_date", "notes", "is_personal"):
            if kwargs.get(opt) is not None:
                body[opt] = kwargs[opt]
        data = await _post("/api/budgeting/expenses/", body)
        return _fmt(data)


# ---------------------------------------------------------------------------
# Packing list
# ---------------------------------------------------------------------------


@tool_parameters(tool_parameters_schema(required=[]))
class GetPackingListTool(Tool):
    """Fetch the packing list for the current trip."""

    @property
    def name(self) -> str:
        return "get_packing_list"

    @property
    def description(self) -> str:
        return "Get the current trip's packing list with items, categories, packed status and quantities."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_: Any) -> str:
        trip_id = _require_trip_id()
        data = await _get(f"/api/trips/{trip_id}/packing/")
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        name=StringSchema("Item name e.g. 'Sunscreen SPF50'"),
        category=StringSchema("Category e.g. toiletries, clothing, electronics (optional)", nullable=True),
        quantity=StringSchema("Quantity e.g. '2 pairs' (optional)", nullable=True),
        notes=StringSchema("Notes (optional)", nullable=True),
        required=["name"],
    )
)
class AddPackingItemTool(Tool):
    """Add an item to the current trip's packing list."""

    @property
    def name(self) -> str:
        return "add_packing_item"

    @property
    def description(self) -> str:
        return "Add an item to the current trip's packing list.  Requires owner or editor role."

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            name=StringSchema("Item name"),
            category=StringSchema("Category (optional)", nullable=True),
            quantity=StringSchema("Quantity (optional)", nullable=True),
            notes=StringSchema("Notes (optional)", nullable=True),
            required=["name"],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()
        body = {k: v for k, v in kwargs.items() if v is not None}
        data = await _post(f"/api/trips/{trip_id}/packing/add/", body)
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        item_id=StringSchema("Packing item ID"),
        packed=BooleanSchema(description="Mark as packed (true) or unpacked (false)", nullable=True),
        name=StringSchema("New item name (optional)", nullable=True),
        quantity=StringSchema("New quantity (optional)", nullable=True),
        notes=StringSchema("New notes (optional)", nullable=True),
        required=["item_id"],
    )
)
class UpdatePackingItemTool(Tool):
    """Update a packing item (name, packed status, quantity, notes)."""

    @property
    def name(self) -> str:
        return "update_packing_item"

    @property
    def description(self) -> str:
        return "Update a packing list item — mark as packed, rename, or change quantity/notes.  Requires owner or editor role."

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            item_id=StringSchema("Packing item ID"),
            packed=BooleanSchema(description="Packed flag", nullable=True),
            name=StringSchema("New name (optional)", nullable=True),
            quantity=StringSchema("New quantity (optional)", nullable=True),
            notes=StringSchema("New notes (optional)", nullable=True),
            required=["item_id"],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        item_id = kwargs.pop("item_id")
        body = {k: v for k, v in kwargs.items() if v is not None}
        if not body:
            return "Nothing to update."
        data = await _patch(f"/api/trips/packing/{item_id}/", body)
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        destination=StringSchema("Destination city or country (optional)", nullable=True),
        trip_type=StringSchema(
            "Trip type hint (optional) e.g. beach | business | hiking | family",
            nullable=True,
        ),
        required=[],
    )
)
class GeneratePackingListTool(Tool):
    """Ask the Traverz backend to AI-generate a packing list for the current trip."""

    @property
    def name(self) -> str:
        return "generate_packing_list"

    @property
    def description(self) -> str:
        return (
            "Use the Traverz AI to generate a packing list based on the trip destination and type.  "
            "Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            destination=StringSchema("Destination city (optional)", nullable=True),
            trip_type=StringSchema("Trip type hint (optional)", nullable=True),
            required=[],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()
        body = {k: v for k, v in kwargs.items() if v is not None}
        data = await _post(f"/api/trips/{trip_id}/packing/generate/", body)
        return _fmt(data)


# ---------------------------------------------------------------------------
# Trip members
# ---------------------------------------------------------------------------


@tool_parameters(tool_parameters_schema(required=[]))
class GetTripMembersTool(Tool):
    """List the members of the current trip with their roles."""

    @property
    def name(self) -> str:
        return "get_trip_members"

    @property
    def description(self) -> str:
        return "List all members of the current trip with their names, usernames, roles and join dates."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_: Any) -> str:
        trip_id = _require_trip_id()
        data = await _get(f"/api/trips/{trip_id}/")
        members = data.get("members", [])
        return _fmt(members)


# ---------------------------------------------------------------------------
# Flights & hotels search
# ---------------------------------------------------------------------------


@tool_parameters(
    tool_parameters_schema(
        from_airport=StringSchema("Departure airport IATA code e.g. SIN"),
        to_airport=StringSchema("Arrival airport IATA code e.g. HND"),
        departure_date=StringSchema("Departure date YYYY-MM-DD"),
        return_date=StringSchema("Return date YYYY-MM-DD (optional, omit for one-way)", nullable=True),
        adults=StringSchema("Number of adult passengers (default 1)", nullable=True),
        cabin_class=StringSchema(
            "Cabin class (optional): economy | premium_economy | business | first",
            enum=["economy", "premium_economy", "business", "first"],
            nullable=True,
        ),
        currency_code=StringSchema("Currency code e.g. SGD (optional)", nullable=True),
        required=["from_airport", "to_airport", "departure_date"],
    )
)
class SearchFlightsTool(Tool):
    """Search for flights via Booking.com integration."""

    @property
    def name(self) -> str:
        return "search_flights"

    @property
    def description(self) -> str:
        return (
            "Search for flights between two airports on given dates.  "
            "Returns flight options with prices, duration and stops."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            from_airport=StringSchema("Departure IATA code"),
            to_airport=StringSchema("Arrival IATA code"),
            departure_date=StringSchema("YYYY-MM-DD"),
            return_date=StringSchema("Return date (optional)", nullable=True),
            adults=StringSchema("Passenger count (optional)", nullable=True),
            cabin_class=StringSchema(
                "Cabin class",
                enum=["economy", "premium_economy", "business", "first"],
                nullable=True,
            ),
            currency_code=StringSchema("Currency code (optional)", nullable=True),
            required=["from_airport", "to_airport", "departure_date"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        trip_id = _require_trip_id()
        body: dict[str, Any] = {
            "from_airport": kwargs["from_airport"],
            "to_airport": kwargs["to_airport"],
            "departure_date": kwargs["departure_date"],
            "adults": int(kwargs.get("adults") or 1),
            "cabin_class": kwargs.get("cabin_class") or "economy",
            "stops": "nonstop_only",
        }
        if kwargs.get("return_date"):
            body["return_date"] = kwargs["return_date"]
        if kwargs.get("currency_code"):
            body["currency_code"] = kwargs["currency_code"]
        data = await _post(f"/api/bookings/trips/{trip_id}/search-booking-com-flights/", body)
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        city=StringSchema("City name to search hotels in"),
        checkin=StringSchema("Check-in date YYYY-MM-DD"),
        checkout=StringSchema("Check-out date YYYY-MM-DD"),
        adults=StringSchema("Number of guests (default 2)", nullable=True),
        rooms=StringSchema("Number of rooms (default 1)", nullable=True),
        required=["city", "checkin", "checkout"],
    )
)
class SearchHotelsTool(Tool):
    """Search for hotels via Booking.com integration."""

    @property
    def name(self) -> str:
        return "search_hotels"

    @property
    def description(self) -> str:
        return (
            "Search for hotels in a city for given dates.  "
            "Returns options with names, prices, ratings and availability."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            city=StringSchema("City name"),
            checkin=StringSchema("Check-in date YYYY-MM-DD"),
            checkout=StringSchema("Check-out date YYYY-MM-DD"),
            adults=StringSchema("Guests (optional)", nullable=True),
            rooms=StringSchema("Rooms (optional)", nullable=True),
            required=["city", "checkin", "checkout"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        trip_id = _require_trip_id()
        body: dict[str, Any] = {
            "city": kwargs["city"],
            "checkin": kwargs["checkin"],
            "checkout": kwargs["checkout"],
            "adults": int(kwargs.get("adults") or 2),
            "rooms": int(kwargs.get("rooms") or 1),
        }
        data = await _post(f"/api/bookings/trips/{trip_id}/search-booking-com-hotels/", body)
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        status=StringSchema(
            "Filter by status: upcoming | past | all (default: upcoming)",
            enum=["upcoming", "past", "all"],
            nullable=True,
        ),
        required=[],
    )
)
class ListUserTripsTool(Tool):
    """List all trips the current user is a member of.

    Use this when there is no active trip context (e.g. on WhatsApp) to let
    the user choose which trip they want to discuss.
    """

    @property
    def name(self) -> str:
        return "list_user_trips"

    @property
    def description(self) -> str:
        return (
            "List the current user's trips.  "
            "Use this when no trip is selected yet to let the user choose a trip.  "
            "Returns trip ID, title, destination and dates for each trip."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            status=StringSchema(
                "upcoming | past | all (default: upcoming)",
                enum=["upcoming", "past", "all"],
                nullable=True,
            ),
            required=[],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        jwt = _ctx.user_jwt.get()
        if not jwt:
            return "No user JWT available.  The user needs to authenticate first."
        status = kwargs.get("status") or "upcoming"
        data = await _get(f"/api/trips/?status={status}")
        trips = data if isinstance(data, list) else data.get("results", data)
        compact = [
            {
                "id": t.get("id"),
                "title": t.get("title"),
                "start_date": t.get("start_date"),
                "end_date": t.get("end_date"),
                "destination": t.get("destination_city") or t.get("title"),
                "status": t.get("status"),
            }
            for t in (trips or [])
        ]
        return _fmt(compact)


# ---------------------------------------------------------------------------
# Dynamic skill dispatcher  — driven by backend-hosted skills manifest
# ---------------------------------------------------------------------------

from nanobot.traverz import skills_manifest as _skills_manifest


def _expand_path_template(template: str, variables: dict[str, Any]) -> str:
    """Replace {placeholders} in a path template with values from `variables`.

    The trip_id placeholder is auto-filled from context if not provided.
    """
    out = template
    if "{trip_id}" in out:
        tid = variables.get("trip_id") or _ctx.trip_id.get()
        if not tid:
            raise ValueError("This skill requires a trip_id, but no trip context is set.")
        out = out.replace("{trip_id}", str(tid))
    for k, v in (variables or {}).items():
        ph = "{" + str(k) + "}"
        if ph in out and v is not None:
            out = out.replace(ph, str(v))
    if "{" in out:
        raise ValueError(f"Path template still has unfilled placeholders: {out}")
    return out


@tool_parameters({
    "type": "object",
    "properties": {
        "domain": {
            "type": "string",
            "description": "Optional filter, e.g. 'pal', 'budgeting', 'documents', 'cities'.",
        },
    },
    "required": [],
})
class DiscoverSkillsTool(Tool):
    """List the skills exposed by the backend manifest.

    Use this if you need to discover capabilities beyond the typed tools —
    e.g. PAL events, document operations, settlements, or anything new
    that the backend has just added.  After listing, invoke a skill by id
    using `traverz_api`.
    """

    @property
    def name(self) -> str:
        return "discover_skills"

    @property
    def description(self) -> str:
        return (
            "List the Traverz backend skills available to you.  "
            "Returns id, mode (generic|trip|both), description and write-flag for each.  "
            "After picking one, invoke it via traverz_api(skill_id=..., variables=..., body=...)."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        await _skills_manifest.ensure_loaded()
        manifest = _skills_manifest.get()
        domain = (kwargs.get("domain") or "").strip().lower()
        skills = list(manifest.skills_by_id.values())
        if domain:
            skills = [s for s in skills if domain in s.get("path", "").lower() or domain in s.get("id", "").lower()]
        compact = [
            {
                "id": s["id"],
                "name": s.get("name"),
                "mode": s.get("mode"),
                "method": s.get("method"),
                "path": s.get("path"),
                "write": s.get("write", False),
                "description": s.get("description"),
            }
            for s in skills
        ]
        return _fmt({"version": manifest.version, "count": len(compact), "skills": compact})


@tool_parameters({
    "type": "object",
    "properties": {
        "skill_id": {
            "type": "string",
            "description": "The skill id from discover_skills (e.g. 'list_pal_events', 'add_expense').",
        },
        "variables": {
            "type": "object",
            "description": "Path/query variables to substitute into the URL template (e.g. {trip_id, event_id, pal_event_id}).",
        },
        "body": {
            "type": "object",
            "description": "JSON body for POST/PATCH skills.  Omit for GET/DELETE.",
        },
    },
    "required": ["skill_id"],
})
class TraverzApiTool(Tool):
    """Generic dispatcher to invoke any backend skill from the manifest.

    This lets the bot use newly added backend endpoints without code
    changes — adding a skill to the manifest is enough.
    Safety: write skills require owner/editor role; trip skills require
    a trip context; the skill must exist in the manifest.
    """

    @property
    def name(self) -> str:
        return "traverz_api"

    @property
    def description(self) -> str:
        return (
            "Invoke a Traverz backend skill by id (use discover_skills first to find ids).  "
            "Pass `variables` to fill path placeholders and `body` for POST/PATCH payloads.  "
            "Use this for capabilities not covered by the typed tools (PAL events, settlements, "
            "documents, ideas, city search, etc.)."
        )

    async def execute(self, **kwargs: Any) -> str:
        skill_id = kwargs.get("skill_id")
        if not skill_id:
            return "skill_id is required."

        await _skills_manifest.ensure_loaded()
        skill = _skills_manifest.get().get(skill_id)
        if not skill:
            return f"Unknown skill_id '{skill_id}'.  Call discover_skills to list available skills."

        mode = skill.get("mode", "trip")
        if mode == "trip" and not _ctx.trip_id.get():
            return f"Skill '{skill_id}' requires a trip context, but the session is generic."

        if skill.get("write"):
            allowed_roles = skill.get("roles") or ["owner", "editor"]
            role = _ctx.user_role.get() or "viewer"
            if role not in allowed_roles:
                return (
                    f"You have '{role}' access; the '{skill_id}' skill requires one of "
                    f"{allowed_roles}."
                )

        try:
            path = _expand_path_template(skill["path"], kwargs.get("variables") or {})
        except ValueError as e:
            return str(e)

        method = (skill.get("method") or "GET").upper()
        body = kwargs.get("body") or {}

        try:
            if method == "GET":
                # Use any non-path variables as query parameters
                query = {k: v for k, v in (kwargs.get("variables") or {}).items()
                         if "{" + k + "}" not in skill["path"]}
                data = await _get(path, params=query or None)
            elif method == "POST":
                data = await _post(path, body)
            elif method == "PATCH":
                data = await _patch(path, body)
            elif method == "DELETE":
                return await _delete(path)
            else:
                return f"Unsupported HTTP method '{method}' on skill '{skill_id}'."
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise AuthExpiredError()
            try:
                err_body = e.response.json()
            except Exception:
                err_body = e.response.text
            return f"Backend returned {e.response.status_code} for {skill_id}: {err_body}"

        return _fmt(data)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@tool_parameters(tool_parameters_schema(required=[]))
class ListDocumentsTool(Tool):
    """List documents attached to the current trip (tickets, confirmations, visas, etc.)."""

    @property
    def name(self) -> str:
        return "list_documents"

    @property
    def description(self) -> str:
        return (
            "List all documents uploaded to the current trip — hotel confirmations, "
            "flight tickets, visa copies, etc.  Returns id, filename, type and URL."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **_: Any) -> str:
        trip_id = _require_trip_id()
        data = await _get(f"/api/trips/{trip_id}/documents/")
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        document_id=StringSchema("ID of the document to extract data from"),
        required=["document_id"],
    )
)
class ExtractDocumentTool(Tool):
    """Ask the Traverz AI to extract structured data from a trip document.

    This triggers OCR + LLM extraction on a previously uploaded booking
    confirmation, ticket or itinerary PDF/image and returns the parsed fields
    (dates, confirmation numbers, prices, etc.).  Call list_documents first to
    get the document id.
    """

    @property
    def name(self) -> str:
        return "extract_document"

    @property
    def description(self) -> str:
        return (
            "Extract structured booking/reservation data from an already-uploaded trip document.  "
            "Returns parsed fields: dates, confirmation numbers, prices, passenger names, etc.  "
            "Use list_documents to find the document_id first."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            document_id=StringSchema("Document ID (from list_documents)"),
            required=["document_id"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        trip_id = _require_trip_id()
        document_id = kwargs["document_id"]
        data = await _post(f"/api/trips/{trip_id}/extract-document/", {"document_id": document_id})
        return _fmt(data)


@tool_parameters(
    tool_parameters_schema(
        document_id=StringSchema(
            "ID of the document whose extracted data should be applied to the trip. "
            "This is the document_id returned by extract_document. "
            "Use this whenever you have a document_id."
        ),
        extracted_data=StringSchema(
            "JSON-serialised extracted data to apply — ONLY use this when you do NOT have "
            "a document_id (e.g. when you read booking details directly from an image or "
            "message). Must contain an 'events' array."
        ),
        required=[],
    )
)
class ApplyExtractedDataTool(Tool):
    """Apply extracted document data to the trip (creates events, updates details).

    After extract_document returns structured data, confirm the key details with
    the user and call this tool to create the corresponding itinerary events,
    update trip dates, or store booking references.

    Prefer passing document_id (from extract_document response) over re-serialising
    the full JSON — it avoids data loss from truncation.
    """

    @property
    def name(self) -> str:
        return "apply_extracted_data"

    @property
    def description(self) -> str:
        return (
            "Apply previously extracted document data to the trip — creates itinerary events "
            "for bookings (flights, hotels, tours) found in the document.  "
            "Pass document_id when available (returned by extract_document).  "
            "Pass extracted_data only when working from inline vision/text with no document_id.  "
            "ALWAYS show the extracted data to the user and confirm before calling this.  "
            "Requires owner or editor role."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            document_id=StringSchema(
                "Document ID returned by extract_document (preferred)"
            ),
            extracted_data=StringSchema(
                "JSON string of extracted data (fallback when no document_id)"
            ),
            required=[],
        )

    async def execute(self, **kwargs: Any) -> str:
        _require_write()
        trip_id = _require_trip_id()

        document_id = kwargs.get("document_id")
        if document_id:
            # Preferred path: let backend fetch from DB — no JSON round-tripping.
            result = await _post(
                f"/api/trips/{trip_id}/apply-extracted-data/",
                {"document_id": document_id},
            )
            return _fmt(result)

        # Fallback: caller supplied raw extracted_data (e.g. from vision).
        import json as _json_mod
        raw = kwargs.get("extracted_data", "{}")
        try:
            data_payload = _json_mod.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            data_payload = {}
        # Unwrap if LLM passed the full extract_document API response:
        # {success, extracted_data: {events, notes, tags}, document_id, ...}
        if isinstance(data_payload, dict) and "extracted_data" in data_payload:
            data_payload = data_payload["extracted_data"]
        result = await _post(
            f"/api/trips/{trip_id}/apply-extracted-data/",
            {"extracted_data": data_payload},
        )
        return _fmt(result)


# ---------------------------------------------------------------------------
# Affiliate partner search tools
# ---------------------------------------------------------------------------

import urllib.parse as _urlparse


@tool_parameters(
    tool_parameters_schema(
        city=StringSchema("City name to search hotels in"),
        checkin=StringSchema("Check-in date YYYY-MM-DD"),
        checkout=StringSchema("Check-out date YYYY-MM-DD"),
        adults=StringSchema("Number of guests (default 2)", nullable=True),
        rooms=StringSchema("Number of rooms (default 1)", nullable=True),
        currency_code=StringSchema("Currency code e.g. USD, SGD (optional)", nullable=True),
        required=["city", "checkin", "checkout"],
    )
)
class SearchTripComHotelsTool(Tool):
    """Search hotels on Trip.com and return an affiliate booking link."""

    @property
    def name(self) -> str:
        return "search_trip_com_hotels"

    @property
    def description(self) -> str:
        return (
            "Search for hotels on Trip.com for a given city and dates. "
            "Returns an affiliate search link so the user can browse options and book directly. "
            "Prefer this over search_hotels when the user wants hotel options."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            city=StringSchema("City name"),
            checkin=StringSchema("Check-in date YYYY-MM-DD"),
            checkout=StringSchema("Check-out date YYYY-MM-DD"),
            adults=StringSchema("Guests (optional)", nullable=True),
            rooms=StringSchema("Rooms (optional)", nullable=True),
            currency_code=StringSchema("Currency code e.g. USD (optional)", nullable=True),
            required=["city", "checkin", "checkout"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        city = kwargs["city"]
        checkin = kwargs["checkin"].replace("-", "")
        checkout = kwargs["checkout"].replace("-", "")
        adults = int(kwargs.get("adults") or 2)
        rooms = int(kwargs.get("rooms") or 1)
        currency = kwargs.get("currency_code") or "USD"
        alliance_id = os.environ.get("TRIP_COM_ALLIANCE_ID", "")
        sid = os.environ.get("TRIP_COM_SID", "")

        params: dict[str, Any] = {
            "city": city,
            "checkIn": checkin,
            "checkOut": checkout,
            "adult": adults,
            "rooms": rooms,
            "curr": currency,
        }
        if alliance_id:
            params["allianceid"] = alliance_id
        if sid:
            params["sid"] = sid

        url = f"https://www.trip.com/hotels/list?{_urlparse.urlencode(params)}"
        return _fmt({
            "platform": "Trip.com",
            "search_type": "hotels",
            "city": city,
            "checkin": kwargs["checkin"],
            "checkout": kwargs["checkout"],
            "adults": adults,
            "rooms": rooms,
            "booking_link": url,
            "note": "Share this link with the user to browse and book hotels on Trip.com.",
        })


@tool_parameters(
    tool_parameters_schema(
        from_airport=StringSchema("Departure airport IATA code e.g. SIN"),
        to_airport=StringSchema("Arrival airport IATA code e.g. HND"),
        departure_date=StringSchema("Departure date YYYY-MM-DD"),
        return_date=StringSchema("Return date YYYY-MM-DD (optional, omit for one-way)", nullable=True),
        adults=StringSchema("Number of adult passengers (default 1)", nullable=True),
        cabin_class=StringSchema(
            "Cabin class (optional): economy | premium_economy | business | first",
            enum=["economy", "premium_economy", "business", "first"],
            nullable=True,
        ),
        currency_code=StringSchema("Currency code e.g. USD (optional)", nullable=True),
        required=["from_airport", "to_airport", "departure_date"],
    )
)
class SearchTripComFlightsTool(Tool):
    """Search flights on Trip.com and return an affiliate booking link."""

    @property
    def name(self) -> str:
        return "search_trip_com_flights"

    @property
    def description(self) -> str:
        return (
            "Search for flights on Trip.com between two airports. "
            "Returns an affiliate search link with real-time fares. "
            "Prefer this over search_flights when the user wants to browse and book flights."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            from_airport=StringSchema("Departure IATA code"),
            to_airport=StringSchema("Arrival IATA code"),
            departure_date=StringSchema("YYYY-MM-DD"),
            return_date=StringSchema("Return date (optional)", nullable=True),
            adults=StringSchema("Passenger count (optional)", nullable=True),
            cabin_class=StringSchema(
                "Cabin class",
                enum=["economy", "premium_economy", "business", "first"],
                nullable=True,
            ),
            currency_code=StringSchema("Currency code (optional)", nullable=True),
            required=["from_airport", "to_airport", "departure_date"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        from_airport = kwargs["from_airport"].upper()
        to_airport = kwargs["to_airport"].upper()
        departure_date = kwargs["departure_date"].replace("-", "")
        return_date = kwargs.get("return_date") or ""
        adults = int(kwargs.get("adults") or 1)
        cabin_class = kwargs.get("cabin_class") or "economy"
        currency = kwargs.get("currency_code") or "USD"
        alliance_id = os.environ.get("TRIP_COM_ALLIANCE_ID", "")
        sid = os.environ.get("TRIP_COM_SID", "")

        seat_type_map = {"economy": 1, "premium_economy": 4, "business": 2, "first": 3}
        seat_type = seat_type_map.get(cabin_class, 1)

        params: dict[str, Any] = {
            "depCity": from_airport,
            "arrCity": to_airport,
            "depDate": departure_date,
            "adultNum": adults,
            "seatType": seat_type,
            "curr": currency,
            "tripType": 2 if return_date else 1,
        }
        if return_date:
            params["retDate"] = return_date.replace("-", "")
        if alliance_id:
            params["allianceid"] = alliance_id
        if sid:
            params["sid"] = sid

        url = f"https://www.trip.com/flights/search?{_urlparse.urlencode(params)}"
        return _fmt({
            "platform": "Trip.com",
            "search_type": "flights",
            "from": from_airport,
            "to": to_airport,
            "departure_date": kwargs["departure_date"],
            "return_date": kwargs.get("return_date"),
            "adults": adults,
            "cabin_class": cabin_class,
            "booking_link": url,
            "note": "Share this link with the user to view and book flights on Trip.com.",
        })


@tool_parameters(
    tool_parameters_schema(
        pickup_city=StringSchema("Pickup city name"),
        dropoff_city=StringSchema("Drop-off city (optional, defaults to pickup city)", nullable=True),
        pickup_date=StringSchema("Pickup date YYYY-MM-DD"),
        return_date=StringSchema("Return/drop-off date YYYY-MM-DD"),
        currency_code=StringSchema("Currency code e.g. USD (optional)", nullable=True),
        required=["pickup_city", "pickup_date", "return_date"],
    )
)
class SearchTripComCarRentalTool(Tool):
    """Search car rentals on Trip.com and return an affiliate booking link."""

    @property
    def name(self) -> str:
        return "search_trip_com_car_rental"

    @property
    def description(self) -> str:
        return (
            "Search for car rentals on Trip.com for a given destination and dates. "
            "Returns an affiliate search link. Use for any car hire or rental requests."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            pickup_city=StringSchema("Pickup city name"),
            dropoff_city=StringSchema("Drop-off city (optional)", nullable=True),
            pickup_date=StringSchema("Pickup date YYYY-MM-DD"),
            return_date=StringSchema("Return date YYYY-MM-DD"),
            currency_code=StringSchema("Currency code (optional)", nullable=True),
            required=["pickup_city", "pickup_date", "return_date"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        pickup_city = kwargs["pickup_city"]
        dropoff_city = kwargs.get("dropoff_city") or pickup_city
        pickup_date = kwargs["pickup_date"].replace("-", "")
        return_date = kwargs["return_date"].replace("-", "")
        currency = kwargs.get("currency_code") or "USD"
        alliance_id = os.environ.get("TRIP_COM_ALLIANCE_ID", "")
        sid = os.environ.get("TRIP_COM_SID", "")

        params: dict[str, Any] = {
            "curr": currency,
            "pickupCity": pickup_city,
            "returnCity": dropoff_city,
            "pickupDate": pickup_date,
            "returnDate": return_date,
        }
        if alliance_id:
            params["allianceid"] = alliance_id
        if sid:
            params["sid"] = sid

        url = f"https://www.trip.com/car/?{_urlparse.urlencode(params)}"
        return _fmt({
            "platform": "Trip.com",
            "search_type": "car_rental",
            "pickup_city": pickup_city,
            "dropoff_city": dropoff_city,
            "pickup_date": kwargs["pickup_date"],
            "return_date": kwargs["return_date"],
            "booking_link": url,
            "note": "Share this link with the user to browse car rental options on Trip.com.",
        })


@tool_parameters(
    tool_parameters_schema(
        destination=StringSchema("Destination city or attraction name"),
        activity=StringSchema(
            "Activity or experience type e.g. 'boat tour', 'food tour', 'cooking class' (optional)",
            nullable=True,
        ),
        date=StringSchema("Activity date YYYY-MM-DD (optional)", nullable=True),
        currency_code=StringSchema("Currency code e.g. USD, EUR (optional)", nullable=True),
        required=["destination"],
    )
)
class SearchGetYourGuideTool(Tool):
    """Search experiences and tours on GetYourGuide and return an affiliate link."""

    @property
    def name(self) -> str:
        return "search_getyourguide"

    @property
    def description(self) -> str:
        return (
            "Search for experiences, tours, and activities on GetYourGuide. "
            "Returns an affiliate link to browse options. "
            "Use for any experience, tour, activity, attraction, or local adventure requests."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            destination=StringSchema("Destination city or attraction"),
            activity=StringSchema("Activity type (optional)", nullable=True),
            date=StringSchema("Date YYYY-MM-DD (optional)", nullable=True),
            currency_code=StringSchema("Currency code (optional)", nullable=True),
            required=["destination"],
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        destination = kwargs["destination"]
        activity = kwargs.get("activity") or ""
        date = kwargs.get("date")
        currency = kwargs.get("currency_code") or "USD"
        partner_id = os.environ.get("GETYOURGUIDE_PARTNER_ID", "")

        search_query = f"{destination} {activity}".strip()
        params: dict[str, Any] = {
            "q": search_query,
            "currency": currency,
            "utm_source": "traverz",
            "utm_medium": "affiliate",
        }
        if date:
            params["date_from"] = date
        if partner_id:
            params["partner_id"] = partner_id

        url = f"https://www.getyourguide.com/s/?{_urlparse.urlencode(params)}"
        return _fmt({
            "platform": "GetYourGuide",
            "search_type": "experiences",
            "destination": destination,
            "activity": activity or destination,
            "date": date,
            "booking_link": url,
            "note": "Share this link with the user to browse and book experiences on GetYourGuide.",
        })


# ---------------------------------------------------------------------------
# Expose all tools
# ---------------------------------------------------------------------------

ALL_TRAVERZ_TOOLS: list[type[Tool]] = [
    GetTripTool,
    UpdateTripTool,
    GetItineraryTool,
    SearchAttractionTool,
    AddEventTool,
    BulkAddEventsTool,
    UpdateEventTool,
    DeleteEventTool,
    GetBudgetTool,
    AddExpenseTool,
    GetPackingListTool,
    AddPackingItemTool,
    UpdatePackingItemTool,
    GeneratePackingListTool,
    GetTripMembersTool,
    SearchFlightsTool,
    SearchHotelsTool,
    SearchTripComHotelsTool,
    SearchTripComFlightsTool,
    SearchTripComCarRentalTool,
    SearchGetYourGuideTool,
    ListUserTripsTool,
    ListDocumentsTool,
    ExtractDocumentTool,
    ApplyExtractedDataTool,
    DiscoverSkillsTool,
    TraverzApiTool,
]
