"""Bounded aggregate extraction diagnostics, without retaining observation payloads."""

import logging
from collections import Counter

GROUPS = (
    "archive_members",
    "aircraft",
    "aircraft_exclusions",
    "observations",
    "ignored_observations",
    "airport_matches",
    "segments",
    "local_rejections",
    "interruptions",
    "coverage_gaps",
    "metadata",
    "review_reasons",
    "endpoint_inference",
    "abandoned_segments",
    "boundary",
    "boundary_rejections",
    "invalid_records",
)
LOGGER = logging.getLogger("ingestion.adsblol")


def configure_logging():
    # Keep unrelated HTTP client logs quiet; extraction events go to stderr.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")
    LOGGER.setLevel(logging.INFO)


class Diagnostics:
    def __init__(self):
        self.groups = {name: Counter() for name in GROUPS}
        self.phase = "initializing"
        self.committed = False
        self.aircraft_id = None
        self.observation_index = None

    def count(self, group, reason, amount=1):
        self.groups[group][reason] += amount

    def summary(self):
        return {
            "version": 1,
            "counts": {name: dict(sorted(values.items())) for name, values in self.groups.items()},
        }


def count(diagnostics, group, reason):
    if diagnostics is not None:
        diagnostics.count(group, reason)
