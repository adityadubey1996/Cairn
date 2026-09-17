from server.application.synthesize import _citations


def test_sources_citation_gets_view_href():
    answer = "Discussed in [doc] sources/gchat/eng-standup-2026-08-12-aaa111.md@abcdef01 today."
    out = _citations(answer, context=[])
    assert out == [{
        "path": "sources/gchat/eng-standup-2026-08-12-aaa111.md",
        "sha": "abcdef01",
        "repo": "ai-brain",
        "href": "/api/sources/view"
                "?path=sources/gchat/eng-standup-2026-08-12-aaa111.md"
                "&etag=abcdef01",
    }]


def test_non_sources_citation_unchanged():
    answer = "See wiki/systems/foo.md@1234abcd."
    out = _citations(answer, context=[])
    assert out == [{"path": "wiki/systems/foo.md", "sha": "1234abcd",
                    "repo": None}]
