import httpx2
from fastapi import Request

DEFAULT_HTTP_TIMEOUT = httpx2.Timeout(30.0, connect=10.0)


def bearer_token(request: Request) -> str:
    """The ``Authorization: Bearer`` token, or ``""`` when the request carries none."""
    scheme, _, token = request.headers.get("Authorization", "").strip().partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""
