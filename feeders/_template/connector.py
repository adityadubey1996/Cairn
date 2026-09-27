"""What this connector is, and what it needs from the user.

Discovered by server/connectors.py — no shared list to edit.
"""
from server.connectors import Connector, Field

SPEC = Connector(
    id="template",                       # also the sources/<id>/ directory name
    name="Template",
    kind="template",
    description="One line the Connect screen shows under the name",
    module="feeders.template.sync",
    configured=lambda: True,             # cheap local check, no network
    auth="token",                        # oauth | token | browser | none
    # Rendered by the Connect form in this order. `secret=True` is stored by
    # server/credentials.py and never reaches the row the UI lists.
    fields=(
        Field("site", "Site URL", placeholder="yourteam.example.com"),
        Field("token", "API token", secret=True, placeholder="paste your token"),
    ),
    setup="Create a read-only API token in your account settings, then paste it here.",
)
