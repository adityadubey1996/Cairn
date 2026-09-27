"""What the Slack connector is, and what it needs from the user.

Discovered by server/connectors.py — no shared list to edit.
"""
from server.connectors import Connector, Field

SPEC = Connector(
    id="slack",
    name="Slack",
    kind="slack",
    description="Channel history from a Slack workspace you are a member of",
    module="feeders.slack.sync",
    # Nothing process-wide to check: the app credentials and the signed-in token
    # both belong to a connection.
    configured=lambda: True,
    auth="token",
    # The token is deliberately absent. It arrives from the sign-in in
    # feeders/slack/router.py and is stored through server/credentials.py, so it
    # is never something a user pastes or a config row holds.
    fields=(
        Field("client_id", "Slack app client ID", placeholder="1234567890.1234567890"),
        Field("client_secret", "Slack app client secret", secret=True,
              placeholder="paste from Basic Information"),
        Field("channels", "Channels to read, comma-separated — empty means every channel you are in",
              required=False, placeholder="general, engineering"),
    ),
    setup="Create a Slack app at api.slack.com/apps for your own workspace, add "
          "https://localhost:3000/slack/callback as an OAuth Redirect URL, and paste its "
          "client ID and client secret here. Then sign in once at "
          "/api/slack/signin/<connection id> — see feeders/slack/SETUP.md.",
)
