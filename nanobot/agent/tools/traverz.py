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
    StringSchema,
    tool_parameters_schema,
)
from nanobot.traverz import context as _ctx

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BACKEND_URL = os.environ.get("TRAVERZ_BACKEND_URL", "https://api.traverz.ai")
_TIMEOUT = 20  # seconds


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


async def _get(path: str, params: dict | None = None) -> Any:
    url = f"{_BACKEND_URL}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, headers=_headers(), params=params)
    resp.raise_for_status()
    return resp.json()


async def _post(path: str, body: dict) -> Any:
    url = f"{_BACKEND_URL}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, headers=_headers(), content=json.dumps(body))
    resp.raise_for_status()
    return resp.json()


async def _patch(path: str, body: dict) -> Any:
    url = f"{_BACKEND_URL}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.patch(url, headers=_headers(), content=json.dumps(body))
    resp.raise_for_status()
    return resp.json()


async def _delete(path: str) -> str:
    url = f"{_BACKEND_URL}{path}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.delete(url, headers=_headers())
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
        type=StringSchema(
            "Event type: destination | accommodation | activity | transport | meal | note",
            enum=["destination", "accommodation", "activity", "transport", "meal", "note"],
        ),
        title=StringSchema("Event title"),
        start_datetime=StringSchema("ISO 8601 datetime e.g. 2025-06-15T09:00:00"),
        end_datetime=StringSchema("ISO 8601 datetime (optional)", nullable=True),
        location_address=StringSchema("Address or place name (optional)", nullable=True),
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


@tool_parameters(
    tool_parameters_schema(
        event_id=StringSchema("Event ID to update"),
        title=StringSchema("New title (optional)", nullable=True),
        start_datetime=StringSchema("New ISO 8601 start datetime (optional)", nullable=True),
        end_datetime=StringSchema("New ISO 8601 end datetime (optional)", nullable=True),
        location_address=StringSchema("New location address (optional)", nullable=True),
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
            location_address=StringSchema("New location address (optional)", nullable=True),
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
# Expose all tools
# ---------------------------------------------------------------------------

ALL_TRAVERZ_TOOLS: list[type[Tool]] = [
    GetTripTool,
    UpdateTripTool,
    GetItineraryTool,
    AddEventTool,
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
    ListUserTripsTool,
]
