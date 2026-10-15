"""Admin exposes source status only; producer writes live in ingestion.jobs."""

from ingestion.jobs import add_ingestion_commands as add_commands
from ingestion.jobs import run_ingestion as run_ingestion


def add_ingestion_commands(commands):
    return add_commands(commands, writable=False)
