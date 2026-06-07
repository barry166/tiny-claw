"""Command-line entrypoint for tiny-claw."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Iterable, Sequence
from typing import cast

from tiny_claw import __version__
from tiny_claw._internal.app import Application, build_application
from tiny_claw._internal.engine.main_loop import RunMode
from tiny_claw._internal.errors import ExitCode, TinyClawError
from tiny_claw._internal.logging_config import configure_logging
from tiny_claw._internal.server import config_from_settings, serve
from tiny_claw._internal.settings import Settings

Handler = Callable[[argparse.Namespace, Application], int]

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
RUN_MODES = ("act", "plan", "think", "plan-act")


class ChineseHelpFormatter(argparse.RawTextHelpFormatter):
    def _format_usage(
        self,
        usage: str | None,
        actions: Iterable[argparse.Action],
        groups: Iterable[argparse._MutuallyExclusiveGroup],
        prefix: str | None,
    ) -> str:
        translated_prefix = "用法: " if prefix is None or prefix == "usage: " else prefix
        return super()._format_usage(usage, actions, groups, translated_prefix)

    def start_section(self, heading: str | None) -> None:
        translated = {
            "positional arguments": "位置参数",
            "options": "选项",
            "optional arguments": "选项",
        }.get(heading or "", heading)
        super().start_section(translated)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tiny-claw",
        description=(
            "Tiny Claw：面向项目工作区的智能编码 CLI。\n"
            "默认使用 OpenAI provider；可通过 .env 或环境变量配置模型、工具、会话和状态目录。"
        ),
        epilog=(
            "常用示例:\n"
            "  tiny-claw health\n"
            '  tiny-claw run --mode plan "帮我设计一个 Next.js 基础框架"\n'
            '  tiny-claw run --mode plan-act --session nextjs "按照计划继续执行"\n'
            "  tiny-claw serve --host 0.0.0.0 --port 8000\n\n"
            "常用环境变量:\n"
            "  OPENAI_API_KEY / OPENAI_KEY        默认 OpenAI provider 的 API Key\n"
            "  TINY_CLAW_PROVIDER                provider 名称：openai、claude、anthropic、echo\n"
            "  TINY_CLAW_ENABLED_TOOLS           启用工具：read,write,edit,bash\n"
            "  TINY_CLAW_WORKDIR                 指定项目工作目录；默认当前执行目录\n"
            "  TINY_CLAW_STATE_DIR               指定状态目录；默认 ~/.tiny-claw"
        ),
        formatter_class=ChineseHelpFormatter,
        add_help=False,
    )
    parser._positionals.title = "命令"
    parser._optionals.title = "全局选项"
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示帮助信息并退出。",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="显示版本号并退出。",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=None,
        metavar="LEVEL",
        help="覆盖本次命令的日志级别，可选：DEBUG、INFO、WARNING、ERROR、CRITICAL。",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="<命令>",
        title="命令",
        description="可用命令；使用 `tiny-claw <命令> -h` 查看子命令详情。",
    )

    health_parser = subparsers.add_parser(
        "health",
        help="检查 provider、工具、状态目录和工作目录。",
        description="检查 tiny-claw 当前配置和运行环境，不会调用模型。",
        epilog=("示例:\n  tiny-claw health\n  TINY_CLAW_PROVIDER=echo tiny-claw health"),
        formatter_class=ChineseHelpFormatter,
        add_help=False,
    )
    health_parser._optionals.title = "选项"
    health_parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示 health 命令帮助并退出。",
    )
    health_parser.set_defaults(handler=_handle_health)

    run_parser = subparsers.add_parser(
        "run",
        help="运行一次智能体对话，支持 act/plan/think/plan-act。",
        description=(
            "运行一次智能体主循环。\n"
            "默认 act 模式会按工具策略执行；plan 模式只创建或恢复 session 计划文件。"
        ),
        epilog=(
            "模式说明:\n"
            "  act       执行模式；模型可看到已启用工具，并按 ReAct 主循环工作。\n"
            "  plan      计划模式；创建/恢复 PLAN.md 和 TODO.md，不暴露工具、不执行任务。\n"
            "  think     思考模式；只分析，不暴露工具、不执行工具。\n"
            "  plan-act  计划执行模式；先创建/恢复计划，再执行当前未完成 TODO。\n\n"
            "示例:\n"
            '  tiny-claw run "解释当前项目结构"\n'
            '  tiny-claw run --mode plan "帮我搭建一下 Next.js 的基本框架吧"\n'
            '  tiny-claw run --mode plan-act --session nextjs "继续执行计划"\n'
            "  TINY_CLAW_ENABLED_TOOLS=read,write,edit,bash \\\n"
            '    tiny-claw run --mode act "修改 README"'
        ),
        formatter_class=ChineseHelpFormatter,
        add_help=False,
    )
    run_parser._positionals.title = "位置参数"
    run_parser._optionals.title = "选项"
    run_parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示 run 命令帮助并退出。",
    )
    run_parser.add_argument(
        "prompt",
        nargs="?",
        default="",
        metavar="提示词",
        help="发送给模型的用户指令；为空时发送空提示。",
    )
    run_parser.add_argument(
        "--max-steps",
        default=20,
        type=_positive_int,
        metavar="N",
        help="本次主循环最多运行多少轮；默认 20。",
    )
    run_parser.add_argument(
        "--mode",
        choices=RUN_MODES,
        default="act",
        metavar="MODE",
        help=(
            "运行模式：act、plan、think、plan-act。\n"
            "  act：执行；plan：只写/读计划；think：只分析；plan-act：按计划执行。"
        ),
    )
    run_parser.add_argument(
        "--session",
        default=None,
        metavar="NAME",
        help="命名 CLI 会话，用于隔离记忆和 session-scoped plan 文件。",
    )
    run_parser.set_defaults(handler=_handle_run)

    serve_parser = subparsers.add_parser(
        "serve",
        help="启动统一 HTTP 事件服务，接收飞书等外部平台回调。",
        description=(
            "启动 tiny-claw 统一 HTTP 事件服务。\n当前包含 GET /health 和 POST /api/events/feishu。"
        ),
        epilog=(
            "示例:\n"
            "  tiny-claw serve --host 0.0.0.0 --port 8000\n"
            "  tiny-claw serve --feishu-path /api/events/feishu-test --mode plan-act\n\n"
            "飞书相关环境变量:\n"
            "  FEISHU_APP_ID / LARK_APP_ID\n"
            "  FEISHU_APP_SECRET / LARK_APP_SECRET\n"
            "  FEISHU_VERIFICATION_TOKEN\n"
            "  FEISHU_ENCRYPT_KEY"
        ),
        formatter_class=ChineseHelpFormatter,
        add_help=False,
    )
    serve_parser._optionals.title = "选项"
    serve_parser.add_argument(
        "-h",
        "--help",
        action="help",
        help="显示 serve 命令帮助并退出。",
    )
    serve_parser.add_argument(
        "--host",
        default=None,
        metavar="HOST",
        help="HTTP 服务监听地址；默认读取 TINY_CLAW_SERVER_HOST 或 0.0.0.0。",
    )
    serve_parser.add_argument(
        "--port",
        default=None,
        type=_positive_int,
        metavar="PORT",
        help="HTTP 服务监听端口；默认读取 TINY_CLAW_SERVER_PORT 或 8000。",
    )
    serve_parser.add_argument(
        "--feishu-path",
        default=None,
        metavar="PATH",
        help="飞书事件回调路径；默认读取 FEISHU_EVENT_PATH 或 /api/events/feishu。",
    )
    serve_parser.add_argument(
        "--max-steps",
        default=20,
        type=_positive_int,
        metavar="N",
        help="每个入站事件最多运行多少轮主循环；默认 20。",
    )
    serve_parser.add_argument(
        "--mode",
        choices=RUN_MODES,
        default="act",
        metavar="MODE",
        help="入站事件的运行模式：act、plan、think、plan-act；默认 act。",
    )
    serve_parser.set_defaults(handler=_handle_serve)

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
    session = app.session_manager.resolve_cli(args.session)
    result = app.run(
        prompt=args.prompt,
        max_steps=args.max_steps,
        mode=RunMode(args.mode),
        session=session,
    )
    print(result.text)
    return int(ExitCode.OK)


def _handle_serve(args: argparse.Namespace, app: Application) -> int:
    import asyncio

    config = config_from_settings(
        app.settings,
        host=args.host,
        port=args.port,
        feishu_event_path=args.feishu_path,
        max_steps=args.max_steps,
        mode=RunMode(args.mode),
    )
    asyncio.run(serve(app, config))
    return int(ExitCode.OK)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from exc

    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be greater than or equal to 1")
    return parsed
