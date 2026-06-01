"""Logging setup for tiny-claw."""

from __future__ import annotations

import logging

COLOR_BLUE = "\033[34m"
COLOR_RESET = "\033[0m"


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=f"{COLOR_BLUE}%(asctime)s{COLOR_RESET} %(levelname)s %(message)s",
        force=True,
    )
