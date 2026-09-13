"""Homework 2 authentication tests.

Both cases run offline: no Langfuse, no Docker, no model provider key. Each
request fails before the agent runs, which is the property being tested.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from server import app as server_app


def _open_session(user_id: int, role: str) -> tuple[str, str]:
    """Create a session the way a client would, returning its id and token."""
    response = server_app.create_session(
        server_app.SessionCreate(user_id=user_id, role=role)
    )
    return response["session_id"], response["token"]


def test_create_session_rejects_a_claimed_role_the_database_denies(world) -> None:
    """User 1 is stored as a shopper, so claiming support must be refused."""
    server_app._SESSIONS.clear()

    with pytest.raises(HTTPException) as exc:
        server_app.create_session(server_app.SessionCreate(user_id=1, role="support"))

    assert exc.value.status_code == 403
    assert server_app._SESSIONS == {}


def test_a_token_cannot_authorize_a_different_session(world) -> None:
    """A token is bound to the session it was issued for."""
    server_app._SESSIONS.clear()
    first_id, first_token = _open_session(1, "shopper")
    second_id, _ = _open_session(2, "shopper")
    assert first_id != second_id

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            server_app.post_message(
                second_id,
                server_app.MessageIn(message="Show my recent orders."),
                authorization=f"Bearer {first_token}",
            )
        )

    assert exc.value.status_code == 403
