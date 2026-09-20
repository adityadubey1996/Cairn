"""Database isolation for the legacy executable offline feeder checks."""
from unittest.mock import patch


def run_offline(feeder, **kwargs):
    with patch.object(feeder.sources_index, "record"), \
         patch.object(feeder.sources_index, "record_failure"), \
         patch.object(feeder.sources_index, "get", return_value=None), \
         patch.object(feeder.sources_index, "attribute"), \
         patch.object(feeder.sources_index, "set_folder"):
        return feeder.run(project_id="offline-check", **kwargs)
