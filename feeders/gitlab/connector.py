"""GitLab issues and merge requests, read over the API with a personal token.

Discovered by server/connectors.py — no shared list to edit.
"""
from server.connectors import Connector, Field

SPEC = Connector(
    id="gitlab",
    name="GitLab",
    kind="gitlab",
    description="Issue and merge request discussions from gitlab.com or your own instance",
    module="feeders.gitlab.sync",
    configured=lambda: True,  # credentials belong to a connection, not the install
    auth="token",
    fields=(
        Field("host", "GitLab host", placeholder="gitlab.com"),
        Field("token", "Personal access token", secret=True,
              placeholder="glpat-… with the read_api scope"),
        Field("projects", "Projects, comma-separated paths — empty means every project you are a member of",
              required=False, placeholder="group/subgroup/project, group/other"),
    ),
    setup="Create a personal access token with the read_api scope under Preferences → Access tokens, then paste it here. See feeders/gitlab/SETUP.md.",
)
