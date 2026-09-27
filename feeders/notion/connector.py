"""What the Notion connector is, and what it needs from the user.

Discovered by server/connectors.py — no shared list to edit.
"""
from server.connectors import Connector, Field

SPEC = Connector(
    id="notion",
    name="Notion",
    kind="notion",
    description="Pages from a Notion workspace shared with your integration",
    module="feeders.notion.sync",
    # Nothing process-wide to check: the secret belongs to a connection, so
    # whether Notion can be read is a per-connection question the probe answers.
    configured=lambda: True,
    auth="token",
    fields=(
        Field("token", "Internal integration secret", secret=True,
              placeholder="ntn_..."),
    ),
    setup="Create an internal integration at notion.so/my-integrations and paste its "
          "secret. Then share every page you want read with it: open the page, "
          "⋯ → Connections → your integration. Pages that are not shared are invisible "
          "to the API — it returns an empty list rather than an error.",
)
