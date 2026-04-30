"""Per-request context variables carrying Traverz trip session data.

These are set by the WebSocket/WhatsApp channel when a message arrives,
and read by traverz tools to authenticate against the backend.
"""

from contextvars import ContextVar

#: The traverz trip ID for the current agent turn.
trip_id: ContextVar[str | None] = ContextVar("traverz_trip_id", default=None)

#: The user's JWT (from traverz-backend short-lived bot token).
user_jwt: ContextVar[str | None] = ContextVar("traverz_user_jwt", default=None)

#: The user's role in the trip: "owner" | "editor" | "viewer".
user_role: ContextVar[str] = ContextVar("traverz_user_role", default="viewer")

#: The user's ID in traverz-backend.
user_id: ContextVar[str | None] = ContextVar("traverz_user_id", default=None)
