"""Jira Cloud issues, read after one Atlassian sign-in.

Discovered by server/connectors.py — no shared list to edit. The OAuthSpec below
is what turns Connect into a login window: server/routers/oauth.py serves
/api/atlassian/authorize and /callback generically for whatever a connector
declares, so this needs no route of its own.
"""
from server.connectors import Choice, Connector, Field, OAuthSpec, ScopeSpec


def _auth():
    """Imported lazily, like every other spec callable, so a broken optional
    dependency can never take the app down at import time."""
    from feeders.jira import auth
    return auth


def _sync():
    from feeders.jira import sync
    return sync


# The vocabularies live with the code that turns them into JQL, so the form and
# the query can never offer different words.
def _sync_types():
    return _sync().ISSUE_TYPES


def _sync_statuses():
    return _sync().STATUS_CATEGORIES


ATLASSIAN_OAUTH = OAuthSpec(
    # Named for this connector, not for Atlassian, so the consent route is
    # /api/jira/authorize and the Connect screen can derive it from the kind
    # without a provider map to maintain. Confluence declares its own: it needs
    # different scopes, so one approval could not honestly cover both.
    provider="jira",
    consent_url=lambda redirect_uri, state: _auth().consent_url(redirect_uri, state),
    exchange=lambda code, redirect_uri: _auth().exchange_code(code, redirect_uri),
    # No secret: Atlassian allows a public PKCE client, so one client id shipped
    # with Cairn is the whole configuration. See feeders/jira/auth.py.
    ready=lambda: _auth().ready(),
)

SPEC = Connector(
    id="jira",
    name="Jira",
    kind="jira",
    description="Issues, descriptions and comments from your Jira sites",
    module="feeders.jira.sync",
    configured=lambda: _auth().signed_in(),
    oauth=ATLASSIAN_OAUTH,
    auth="oauth",
    requires=("ATLASSIAN_CLIENT_ID",),
    # No fields: nothing is typed. A site is chosen from what the sign-in
    # granted, which is also the only list that can be trusted to be right.
    # An API-token connection is still supported for an install that would
    # rather not register anything — see SETUP.md — but it is set through the
    # API, not offered on the form, because a token box next to a sign-in
    # button is how the last version of this screen became unusable.
    fields=(),
    setup="Register one Cairn app at developer.atlassian.com (once, for this install), set ATLASSIAN_CLIENT_ID, then click Connect and sign in. No secret and nothing per user.",
    # Projects, grouped the way the Jira admin already grouped them. Every
    # filter here becomes a JQL clause in scoped_jql() — nothing is applied
    # client-side, so a narrow scope is a smaller request, not a smaller loop.
    scope=ScopeSpec(
        kind="project",
        options=lambda settings: _sync().list_projects(settings),
        note="Cairn reads only the projects you tick. Widening the scope backfills on the next sync.",
        fields=(
            Choice("issue_types", "Issue type", _sync_types(),
                   ("Story", "Bug", "Task", "Epic")),
            Choice("statuses", "Status", _sync_statuses(),
                   ("To Do", "In Progress", "Done")),
            Choice("updated_within_days", "Updated within"),
        ),
    ),
)

# Kept only so an install that pastes a token through the API knows the names
# sync.py reads. Not rendered: SPEC.fields above is deliberately empty.
TOKEN_FIELDS = (
    Field("site", "Site URL", placeholder="yourteam.atlassian.net"),
    Field("email", "Account email", placeholder="you@yourteam.com"),
    Field("token", "API token", secret=True),
)
