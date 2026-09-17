#!/bin/sh
set -e

# Seed the wiki volume from the image, once.
#
# Articles are the artifact — each one cost an LLM call — so they are committed
# to git and baked into the image at /app/wikis. The server writes NEW ones to
# the /data/wikis volume, which starts empty on a fresh deploy.
#
# Without this the committed articles would be invisible: REPO_WIKI_DIR points
# at the volume, and the image copy would sit at a path nothing reads.
#
# Only ever seeds an empty volume. A volume with content is the live wiki and
# may hold articles newer than the image — never overwrite it from a build.
if [ -d /app/wikis ] && [ -z "$(ls -A /data/wikis 2>/dev/null)" ]; then
    echo "seeding /data/wikis from the image ($(find /app/wikis -name '*.md' -not -name '_*' | wc -l | tr -d ' ') articles)"
    cp -a /app/wikis/. /data/wikis/
fi

exec "$@"
