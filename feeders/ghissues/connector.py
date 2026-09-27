"""GitHub issue and pull request threads — not the repo-cloning connector.

feeders/github/ clones code and history; it reads no issues and no pull
requests. This is a separate card with its own token and its own repositories.
"""
from server.connectors import Connector, Field

SPEC = Connector(
    id="ghissues",
    name="GitHub issues",
    kind="ghissues",
    description="Issue and pull request threads, with their comments",
    module="feeders.ghissues.sync",
    # The token belongs to the connection, not the install, so there is
    # nothing local to check — server/credentials.py holds it per connection.
    configured=lambda: True,
    auth="token",
    fields=(
        Field("token", "Personal access token", secret=True,
              placeholder="github_pat_..."),
        Field("repos", "Repositories, comma-separated, as owner/name",
              placeholder="octocat/hello-world, octocat/spoon-knife"),
    ),
    setup="Create a fine-grained personal access token with Issues: read, Pull requests: read and Contents: read, then list the repositories to follow. An org using SAML single sign-on must also authorise the token for that org.",
)
