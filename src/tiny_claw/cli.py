"""Command-line entrypoint for tiny-claw."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Sequence
from typing import cast

from tiny_claw import __version__
from tiny_claw._internal.app import Application, build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.errors import ExitCode, TinyClawError
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.settings import Settings

Handler = Callable[[argparse.Namespace, Application], int]

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
RUN_MODES = ("act", "think", "plan-act")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tiny-claw",
        description="Industrial Python CLI framework skeleton.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=None,
        help="Override TINY_CLAW_LOG_LEVEL for this command.",
    )

    subparsers = parser.add_subparsers(dest="command")

    health_parser = subparsers.add_parser("health", help="Check framework wiring.")
    health_parser.set_defaults(handler=_handle_health)

    run_parser = subparsers.add_parser("run", help="Run one prompt through the main loop.")
    run_parser.add_argument(
        "prompt",
        nargs="?",
        default="",
        help="Prompt text to send to the configured provider.",
    )
    run_parser.add_argument(
        "--max-steps",
        default=20,
        type=_positive_int,
        help="Maximum main-loop steps for this run.",
    )
    run_parser.add_argument(
        "--mode",
        choices=RUN_MODES,
        default="act",
        help=(
            "Run mode: 'think' hides tools; 'act' allows ReAct tool use; "
            "'plan-act' plans first, then acts."
        ),
    )
    run_parser.set_defaults(handler=_handle_run)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast("Handler | None", getattr(args, "handler", None))

    if handler is None:
        parser.print_help()
        return int(ExitCode.OK)

    try:
        settings = Settings.from_env(log_level=getattr(args, "log_level", None))
        configure_logging(settings.log_level)
        app = build_application(settings)
        return handler(args, app)
    except TinyClawError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return int(exc.exit_code)
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Interrupted")
        return int(ExitCode.INTERRUPTED)


def _handle_health(_args: argparse.Namespace, app: Application) -> int:
    print(app.health().render())
    return int(ExitCode.OK)


def _handle_run(args: argparse.Namespace, app: Application) -> int:
    result = app.run(
        prompt=args.prompt,
        max_steps=args.max_steps,
        mode=RunMode(args.mode),
    )
    print(result.text)
    return int(ExitCode.OK)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than or equal to 1")
    return parsed
