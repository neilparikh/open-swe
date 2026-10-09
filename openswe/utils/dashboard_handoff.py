"""What the agent is told when a chat-platform conversation continues on the web."""

from openswe.middleware.require_user_reply import ChatSurface
from openswe.prompts import prompt

DASHBOARD_HANDOFF_SENDER_ID = "system:dashboard-handoff"

_SURFACES: dict[ChatSurface, tuple[str, str]] = {
    "slack": ("Slack", "slack_reply"),
}


def chat_surface_of(name: object) -> ChatSurface | None:
    """The chat platform a thread source or reply surface names, or ``None`` for the web."""
    return "slack" if name == "slack" else None


def dashboard_handoff_body(surface: ChatSurface) -> str:
    surface_name, reply_tool = _SURFACES[surface]
    return prompt("runs/dashboard-handoff", surface_name=surface_name, reply_tool=reply_tool)
