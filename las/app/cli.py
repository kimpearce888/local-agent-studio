"""The ``studio`` command line (spec 7.4).

Exit codes: 0 run succeeded · 1 run failed · 2 invalid plan or arguments ·
3 provider unreachable or authentication failed · 4 waiting at a gate in
non-interactive mode · 5 a cap was reached · 6 run cancelled · 7 internal
error.

S5b: command handlers live in las/app/commands/ (verbatim moves around a
shared context in commands/common.py); this file stays the composition
root - parser, dispatch and the backward-compat import surface.
"""

from __future__ import annotations

import argparse
import sys

from las.app.commands.common import (  # noqa: F401 - re-exported surface
    EXIT_OK, EXIT_RUN_FAILED, EXIT_INVALID, EXIT_PROVIDER, EXIT_GATE,
    EXIT_CAP, EXIT_CANCELLED, EXIT_INTERNAL,
    default_data_dir, _print_json, _load_plan_arg, _registry_for, _now,
    argparse, json, os, sys, Optional, __version__,
    require_module, set_enabled, ModuleDisabledError, ModuleNotInstalledError,
    ModuleRegistry, lint_errors, lint, message, message_with_fix, Planner,
    PRESETS, ProviderRegistry, build_adapter, probe_adapter, detect_servers,
    ProviderError, DriverConfig, Engine, validate_plan,
    Plan, PlanFormatError, plan_variables, substitute_variables, task_from_obj,
    ProviderProfile, Store, TamperedLogError, KNOWN_TOOLS,
)
from las.app.commands.planning_cmds import cmd_plan
from las.app.commands.run_cmds import (
    cmd_run, cmd_list, cmd_inspect, cmd_resume, cmd_gate, cmd_task_control,
    cmd_export, cmd_delete, cmd_replan, cmd_compare, cmd_edit, cmd_verify,
)
from las.app.commands.provider_cmds import cmd_providers, cmd_models
from las.app.commands.library_cmds import cmd_templates, cmd_roles
from las.app.commands.data_cmds import cmd_data, cmd_diagnostics
from las.app.commands.system_cmds import cmd_modules, cmd_selftest, cmd_serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio", description="Local Agent Studio — local-first agent chains")
    parser.add_argument("--version", action="version", version=f"studio {__version__}")
    parser.add_argument("--data-dir", default=default_data_dir(),
                        help="data root (default: platform app data, or LAS_DATA_DIR)")
    # The same flag works after the subcommand (SUPPRESS keeps the value
    # given before the subcommand).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("plan", parents=[common], help="Generate a plan from a goal")
    p.add_argument("goal")
    p.add_argument("-o", "--output", help="write the plan JSON to this path")
    p.add_argument("--granularity", choices=("coarse", "normal", "fine"), default="normal")
    p.add_argument("--attach", action="append", default=[], metavar="FILE",
                   help="attach a file to the goal (repeatable; PLN-7)")
    p.add_argument("--max-tasks", type=int, default=30)
    p.add_argument("--provider")
    p.add_argument("--model")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("run", parents=[common], help="Run a plan or template")
    p.add_argument("plan")
    p.add_argument("--goal", help="override the goal (templates)")
    p.add_argument("--var", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--provider")
    p.add_argument("--planner-model")
    p.add_argument("--worker-model")
    p.add_argument("--on-failure", choices=("ask", "stop", "skip"), default="ask")
    p.add_argument("--cap-tokens", type=int, default=2_000_000)
    p.add_argument("--cap-wall-minutes", type=int, default=480)
    p.add_argument("--context-length", type=int, default=8192)
    p.add_argument("--pause-after", metavar="TASK_ID")
    p.add_argument("--enable-shell", action="store_true")
    p.add_argument("--enable-web", action="store_true")
    p.add_argument("--command-allowlist", help="comma-separated command names")
    p.add_argument("--web-domains", help="comma-separated domains")
    p.add_argument("--force", action="store_true", help="run despite lint warnings")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("list", parents=[common], help="List runs")
    p.add_argument("--status")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("inspect", parents=[common], help="Show a run")
    p.add_argument("run_id")
    p.add_argument("--task")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("resume", parents=[common], help="Resume a paused or failed run")
    p.add_argument("run_id")
    p.add_argument("--provider")
    p.add_argument("--planner-model")
    p.add_argument("--worker-model")
    p.add_argument("--on-failure", choices=("ask", "stop", "skip"), default="ask")
    p.add_argument("--pause-after", metavar="TASK_ID")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("approve", parents=[common], help="Approve a gate")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(func=lambda a: cmd_gate(a, approve=True))

    p = sub.add_parser("reject", parents=[common], help="Reject a gate")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--decision", choices=("stop", "skip", "edit"), default="stop")
    p.add_argument("--reason", default="")
    p.set_defaults(func=lambda a: cmd_gate(a, approve=False))

    for action, help_ in (("retry", "Retry a failed task"),
                          ("skip", "Skip a task (dependents are skipped too)"),
                          ("rerun", "Rerun from a task (later tasks go stale)")):
        p = sub.add_parser(action, parents=[common], help=help_)
        p.add_argument("run_id")
        p.add_argument("task_id")
        if action == "skip":
            p.add_argument("--no-cascade", action="store_true",
                           help="continue dependent tasks instead of skipping them")
        p.set_defaults(func=lambda a, act=action: cmd_task_control(a, act))

    p = sub.add_parser("export", parents=[common], help="Export a run")
    # run_id is optional at the parser level so the capability gate can
    # fire (with an actionable message) before usage errors are reported.
    p.add_argument("run_id", nargs="?")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    p.add_argument("--no-ai-notice", action="store_true")
    p.add_argument("--redact-prompts", action="store_true")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("providers", parents=[common], help="Manage providers")
    p.add_argument("providers_cmd", nargs="?", choices=("list", "add", "detect", "test"),
                   default="test")
    p.add_argument("--name", help="provider id (add)")
    p.add_argument("--preset", default="generic",
                   choices=tuple(PRESETS.keys()) + ("mock",),
                   help="server preset; 'mock' is the offline development provider")
    p.add_argument("--base-url")
    p.add_argument("--auth-env", help="env:VARNAME key reference")
    p.add_argument("--wire", choices=("chat", "responses"), default="chat")
    p.add_argument("--model", help="model id for the capability probe (test)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_providers)

    p = sub.add_parser("selftest", parents=[common], help="Run the built-in three-task self-test")
    p.add_argument("--provider", help="use a configured provider instead of the mock")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_selftest)

    p = sub.add_parser("modules", parents=[common], help="List, enable or disable modules")
    p.add_argument("modules_cmd", nargs="?", choices=("list", "enable", "disable"),
                   default="list")
    p.add_argument("module_id", nargs="?", help="module id (enable/disable)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("templates", parents=[common], help="Manage plan templates (LIB-1)")
    p.add_argument("templates_cmd", nargs="?",
                   choices=("list", "save", "duplicate", "delete", "export"),
                   default="list")
    p.add_argument("template_id", nargs="?", help="template id")
    p.add_argument("plan_file", nargs="?", help="plan JSON file (save)")
    p.add_argument("--name", default="", help="template name (save)")
    p.add_argument("--description", default="", help="template description")
    p.add_argument("--new-name", dest="new_name", default="", help="duplicate name")
    p.add_argument("-o", "--output", default="", help="output file (export)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_templates)

    p = sub.add_parser("roles", parents=[common], help="Manage role profiles (LIB-2)")
    p.add_argument("roles_cmd", nargs="?", choices=("list", "save", "delete"),
                   default="list")
    p.add_argument("profile_id", nargs="?", help="profile id (delete)")
    p.add_argument("file", nargs="?", help="profile JSON file (save)")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("data", parents=[common], help="Data management (SET-2)")
    p.add_argument("data_cmd", nargs="?", choices=("status", "prune", "delete-all"),
                   default="status")
    p.add_argument("--days", type=int, default=None,
                   help="prune runs older than N days (overrides retention)")
    p.add_argument("--confirm", action="store_true",
                   help="required for delete-all")
    p.set_defaults(func=cmd_data)

    p = sub.add_parser("delete", parents=[common],
                       help="Delete one run and its workspace (SET-2)")
    p.add_argument("run_id")
    p.add_argument("--confirm", action="store_true",
                   help="required; deletion is permanent")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("replan", parents=[common],
                       help="Propose and apply a replan of remaining tasks (PLN-6)")
    p.add_argument("run_id")
    p.add_argument("--apply", default="",
                   help="apply a stored proposal plan id (from a previous run of this command)")
    p.add_argument("--provider", default="", help="provider id or name")
    p.add_argument("--model", default="", help="planner model")
    p.set_defaults(func=cmd_replan)

    p = sub.add_parser("compare", parents=[common],
                       help="Compare two runs (OBS-6)")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--json", dest="json_out", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("edit", parents=[common],
                       help="Edit a pending task of a run (RUN-6)")
    p.add_argument("run_id")
    p.add_argument("task_id")
    p.add_argument("--instructions", default="", help="new instructions")
    p.add_argument("--title", default="", help="new title")
    p.add_argument("--model-provider", dest="model_provider", default="",
                   help="per-task model override: provider id")
    p.add_argument("--model-id", dest="model_id", default="",
                   help="per-task model override: model id")
    p.add_argument("--reason", default="", help="why the edit was made")
    p.set_defaults(func=cmd_edit)

    p = sub.add_parser("models", parents=[common],
                       help="Model lifecycle: list, load, unload (PRV-8)")
    p.add_argument("models_cmd", nargs="?", choices=("list", "load", "unload"),
                   default="list")
    p.add_argument("--provider", default="", help="provider id or name")
    p.add_argument("--model", default="", help="model id (load/unload)")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("diagnostics", parents=[common],
                       help="Write a redacted diagnostics bundle (OBS-7)")
    p.add_argument("-o", "--output", default="", help="output file")
    p.set_defaults(func=cmd_diagnostics)

    p = sub.add_parser("serve", parents=[common],
                       help="Serve the engine HTTP API on loopback (spec 7.5)")
    p.add_argument("--port", type=int, default=0, help="port (default: random free)")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("verify", parents=[common], help="Verify a run's event hash chain")
    p.add_argument("run_id")
    p.set_defaults(func=cmd_verify)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except (ModuleDisabledError, ModuleNotInstalledError) as exc:
        print(str(exc))
        return EXIT_INVALID
    except PlanFormatError as exc:
        print(f"PLAN-INVALID: {exc.detail}")
        return EXIT_INVALID
    except ProviderError as exc:
        msg, fix = message_with_fix(exc.code)
        print(f"{exc.code}: {msg}\nFix: {fix}")
        return EXIT_PROVIDER
    except TamperedLogError as exc:
        print(f"INTERNAL: {exc}")
        return EXIT_INTERNAL
    except Exception as exc:  # noqa: BLE001
        print(f"INTERNAL: {type(exc).__name__}: {exc}")
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())


