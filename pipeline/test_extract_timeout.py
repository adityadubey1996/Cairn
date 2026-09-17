#!/usr/bin/env python3
"""Self-check: every extraction subprocess call must carry a timeout, so one
bad file can't hang ingest indefinitely. Run: python3 test_extract_timeout.py"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import ingest  # noqa: E402


def check_pandoc_call_has_a_timeout():
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        class R:
            stdout = "converted"
        return R()
    with patch("ingest.have", return_value=True), \
         patch("ingest.subprocess.run", fake_run):
        ingest.extract_binary(Path("x.docx"))
    assert calls and "timeout" in calls[0], calls


def check_pdftotext_call_has_a_timeout():
    calls = []
    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        class R:
            stdout = "extracted"
        return R()
    with patch("ingest.have", side_effect=lambda cmd: cmd == "pdftotext"), \
         patch("ingest.subprocess.run", fake_run):
        ingest.extract_binary(Path("x.pdf"))
    assert calls and "timeout" in calls[0], calls


if __name__ == "__main__":
    check_pandoc_call_has_a_timeout()
    check_pdftotext_call_has_a_timeout()
    print("ok")
