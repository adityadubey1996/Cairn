# wikis/

Generated articles land here, one folder per source repo.

**This is the artifact.** Everything else Cairn keeps — the index, the database,
the clones under `var/` — is a cache that rebuilds itself. These markdown files
are not: losing them means paying to generate every article again. Commit them,
and on a deployment make sure this path is a persistent volume.

It is empty in a fresh checkout. Add a source from the Connect or Repos screen
and the pipeline writes here.
