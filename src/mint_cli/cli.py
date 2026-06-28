from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys

from . import __version__
from .errors import MintError
from .workflow import (
    clean_module,
    doctor_project,
    healthcheck_module,
    init_project,
    inspect_unit,
    live_smoke_module,
    lint_module,
    new_module,
    next_module,
    parse_module,
    report_module,
    render_module,
    status_module,
)
from .stacks import known_stacks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mint",
        description="Local Codeplain-inspired regenerative coding workflow.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    init_parser = subparsers.add_parser(
        "init",
        help="show or create the Phase 0 project skeleton",
    )
    init_parser.add_argument(
        "--write",
        action="store_true",
        help="create missing mint.yaml, directories, and test scripts",
    )
    init_parser.set_defaults(handler=handle_init)

    parse_parser = subparsers.add_parser(
        "parse",
        help="parse a spec into canonical JSON",
    )
    parse_parser.add_argument("module", help="module name, for example: example")
    parse_parser.set_defaults(handler=handle_parse)

    new_parser = subparsers.add_parser(
        "new",
        help="scaffold a starter spec",
    )
    new_parser.add_argument("module", help="module name, for example: calc-cli")
    new_parser.add_argument(
        "--requires",
        nargs="*",
        default=[],
        metavar="MODULE",
        help="required module specs to depend on",
    )
    new_parser.add_argument(
        "--stack",
        choices=known_stacks(),
        default=None,
        help="target stack for the starter spec; defaults to python-lib",
    )
    new_parser.add_argument(
        "--renderer",
        choices=["local", "deterministic", "model", "anthropic", "claude-cli", "codex-cli"],
        default=None,
        help="optional per-spec renderer override; use a model provider for fresh template-free specs",
    )
    new_parser.add_argument(
        "--model",
        default=None,
        help="required model id or model label when using a model renderer",
    )
    new_parser.add_argument(
        "--prompt-version",
        default=None,
        help="required prompt version when using a model renderer",
    )
    new_parser.set_defaults(handler=handle_new)

    lint_parser = subparsers.add_parser(
        "lint",
        help="check spec quality beyond parsing",
    )
    lint_parser.add_argument("module", help="module name, for example: example")
    lint_parser.set_defaults(handler=handle_lint)

    next_parser = subparsers.add_parser(
        "next",
        help="show the next recommended action for a project or module",
    )
    next_parser.add_argument(
        "module",
        nargs="?",
        help="optional module name, for example: example",
    )
    next_parser.set_defaults(handler=handle_next)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="check project toolchain, scripts, specs, and replay fixtures",
    )
    doctor_parser.set_defaults(handler=handle_doctor)

    healthcheck_parser = subparsers.add_parser(
        "healthcheck",
        help="validate pre-render inputs",
    )
    healthcheck_parser.add_argument("module", help="module name, for example: example")
    healthcheck_parser.set_defaults(handler=handle_healthcheck)

    render_parser = subparsers.add_parser(
        "render",
        help="render functional units",
    )
    render_parser.add_argument("module", help="module name, for example: example")
    render_parser.add_argument("--from", dest="from_unit", metavar="FRN")
    render_parser.add_argument("--range", dest="unit_range", metavar="FRN:FRM")
    render_parser.add_argument("--force", action="store_true")
    render_parser.set_defaults(handler=handle_render)

    live_smoke_parser = subparsers.add_parser(
        "live-smoke",
        help="force a live model render and record replay cassettes",
    )
    live_smoke_parser.add_argument("module", help="module name, for example: calc-cli")
    live_smoke_parser.set_defaults(handler=handle_live_smoke)

    status_parser = subparsers.add_parser(
        "status",
        help="show generated module state",
    )
    status_parser.add_argument("module", help="module name, for example: example")
    status_parser.set_defaults(handler=handle_status)

    report_parser = subparsers.add_parser(
        "report",
        help="print the latest run report",
    )
    report_parser.add_argument("module", help="module name, for example: example")
    report_parser.set_defaults(handler=handle_report)

    inspect_parser = subparsers.add_parser(
        "inspect",
        help="inspect a rendered functional unit",
    )
    inspect_parser.add_argument("module", help="module name, for example: example")
    inspect_parser.add_argument("unit_id", help="functional unit ID, for example: FR1")
    inspect_parser.set_defaults(handler=handle_inspect)

    clean_parser = subparsers.add_parser(
        "clean",
        help="remove generated output after confirmation",
    )
    clean_parser.add_argument("module", help="module name, for example: example")
    clean_parser.add_argument("--yes", action="store_true", help="confirm deletion")
    clean_parser.set_defaults(handler=handle_clean)

    return parser


def handle_init(args: argparse.Namespace) -> int:
    status, output = init_project(write=args.write)
    print(output, end="")
    return status


def handle_parse(args: argparse.Namespace) -> int:
    print(parse_module(args.module), end="")
    return 0


def handle_new(args: argparse.Namespace) -> int:
    status, output = new_module(
        args.module,
        requires=args.requires,
        stack=args.stack,
        renderer=args.renderer,
        model=args.model,
        prompt_version=args.prompt_version,
    )
    print(output, end="")
    return status


def handle_lint(args: argparse.Namespace) -> int:
    status, output = lint_module(args.module)
    print(output, end="")
    return status


def handle_next(args: argparse.Namespace) -> int:
    status, output = next_module(args.module)
    print(output, end="")
    return status


def handle_doctor(_args: argparse.Namespace) -> int:
    status, output = doctor_project()
    print(output, end="")
    return status


def handle_healthcheck(args: argparse.Namespace) -> int:
    status, output = healthcheck_module(args.module)
    print(output, end="")
    return status


def handle_status(args: argparse.Namespace) -> int:
    print(status_module(args.module), end="")
    return 0


def handle_report(args: argparse.Namespace) -> int:
    status, output = report_module(args.module)
    print(output, end="")
    return status


def handle_render(args: argparse.Namespace) -> int:
    status, output = render_module(
        args.module,
        from_unit=args.from_unit,
        unit_range=args.unit_range,
        force=args.force,
    )
    print(output, end="")
    return status


def handle_live_smoke(args: argparse.Namespace) -> int:
    status, output = live_smoke_module(args.module)
    print(output, end="")
    return status


def handle_inspect(args: argparse.Namespace) -> int:
    status, output = inspect_unit(args.module, args.unit_id)
    print(output, end="")
    return status


def handle_clean(args: argparse.Namespace) -> int:
    status, output = clean_module(args.module, yes=args.yes)
    print(output, end="")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not hasattr(args, "handler"):
        parser.print_help()
        return 0

    try:
        return args.handler(args)
    except MintError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
