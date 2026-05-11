"""Shared exception types for nanobot tools."""

from __future__ import annotations


class FatalToolError(Exception):
    """Raised by tools to signal an unrecoverable error.

    Unlike regular tool exceptions (which are fed back to the LLM as a tool
    result so it can retry), a ``FatalToolError`` immediately aborts the agent
    loop.  The exception message is surfaced directly to the user.
    """


class AuthExpiredError(FatalToolError):
    """Raised when a Traverz backend call receives HTTP 401 Unauthorized.

    Indicates the user's JWT has expired; all further backend calls will fail.
    The agent loop is aborted immediately instead of wasting LLM iterations on
    retries that will all fail the same way.
    """

    def __init__(self) -> None:
        super().__init__(
            "Your session has expired. Please re-open the bot from the Traverz app "
            "to refresh your login, then try again."
        )
