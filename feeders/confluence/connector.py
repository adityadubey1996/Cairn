"""Confluence Cloud pages, read through an Atlassian OAuth sign-in.

Discovered by server/connectors.py — no shared list to edit. The OAuthSpec is
what makes server/routers/oauth.py serve /api/confluence/authorize and
/api/confluence/callback for this folder.
"""
from server.connectors import Connector, OAuthSpec


def _auth():
    # Imported lazily for the same reason `module` below is a string: a folder
    # that fails to import must not take the app down with it.
    from feeders.confluence import auth
    return auth


SPEC = Connector(
    id="confluence",
    name="Confluence",
    kind="confluence",
    description="Wiki pages from your Confluence Cloud site",
    module="feeders.confluence.sync",
    configured=lambda: _auth().TOKEN_FILE.is_file(),
    auth="oauth",
    oauth=OAuthSpec(
        provider="confluence",
        consent_url=lambda redirect_uri, state: _auth().consent_url(redirect_uri, state),
        exchange=lambda code, redirect_uri: _auth().exchange_code(code, redirect_uri),
        ready=lambda: _auth().ready(),
    ),
    requires=("CONFLUENCE_CLIENT_ID", "CONFLUENCE_CLIENT_SECRET"),
    setup=("Create an OAuth 2.0 (3LO) app at developer.atlassian.com/console/myapps, add the "
           "Confluence scopes and a callback of http://localhost:8310/api/confluence/callback, "
           "put its client id and secret in .env, then press Connect. "
           "See feeders/confluence/SETUP.md."),
)
