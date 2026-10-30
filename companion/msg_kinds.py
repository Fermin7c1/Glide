"""The kinds of the engine's feed messages ({"kind": ...}), named once for everything that sorts them."""

# what the coach said to the player: the conversation it follows up on, and the in-game overlay's text
COACH_SAID = frozenset(("callout", "answer", "review", "briefing", "reminder"))
# the coach's last few lines in the prompt, and the line a feedback mark points at ("last callout")
RECENT = frozenset(("callout", "answer", "review", "briefing"))
# kept in the message history a feedback mark records (the coach's lines and the engine's own notes)
HISTORY = COACH_SAID | {"info"}
