"""Adaptive Cards the Teams bot sends: answer buttons and what replaces them.

Buttons use ``Action.Execute``: a click arrives as its own ``invoke`` request,
and the bot's response replaces the card in place.
"""

import uuid

from openswe.utils.json_types import JsonObject

ANSWER_VERB = "open_swe.answer"
# Schema 1.4 is the first with Action.Execute.
_SCHEMA = {
    "type": "AdaptiveCard",
    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
    "version": "1.4",
}
# The same limits as Slack's option buttons (`openswe.slack.blocks.option_actions`).
_MAX_OPTIONS = 5
_MAX_LABEL_CHARS = 75


def answer_card(options: list[str]) -> JsonObject | None:
    """Buttons for up to five answers, or ``None`` when no option has text.

    Every button carries the card's own id, so the card is answered once.
    """
    answers = [option.strip() for option in options if option.strip()][:_MAX_OPTIONS]
    if not answers:
        return None
    card_id = str(uuid.uuid4())
    return {
        **_SCHEMA,
        "body": [],
        "actions": [
            {
                "type": "Action.Execute",
                "title": answer[:_MAX_LABEL_CHARS],
                "verb": ANSWER_VERB,
                "data": {"answer": answer, "card": card_id},
            }
            for answer in answers
        ],
    }


def answered_card(answer: str, chooser: str) -> JsonObject:
    """What an answer card becomes once somebody picked ``answer``."""
    return {
        **_SCHEMA,
        "body": [
            {
                "type": "TextBlock",
                "text": f"**{chooser or 'Someone'}** chose **{answer}**",
                "wrap": True,
            }
        ],
    }
