"""S5b: split the host CLI into command modules around a shared context.

Verbatim move of handler bodies out of cli.py into las/app/commands/.
cli.py stays the composition root: parser + dispatch + backward-compat
re-exports (tests import EXIT_* / main / build_parser from it).
"""

import re

SRC = open("las/app/cli.py", encoding="utf-8").read()
L = SRC.splitlines(keepends=True)


def block(start: int, end: int) -> str:
    """1-indexed inclusive slice, trailing blank lines stripped to two."""
    text = "".join(L[start - 1:end])
    text = re.sub(r"\n{3,}$", "\n\n\n", text.rstrip("\n") + "\n\n\n")
    return text


HEADER = block(1, 34)          # docstring + imports
EXITS = block(36, 43)
HELPERS = block(46, 106)       # default_data_dir .. _registry_for
NOW = block(676, 680)
CMD_PLAN = block(107, 207)
RUN_A = block(208, 521)        # cmd_run .. cmd_export
CMD_MODULES = block(522, 560)
CMD_PROVIDERS = block(561, 635)
CMD_SELFTEST = block(636, 668)  # cmd_selftest + cmd_serve
CMD_VERIFY = block(669, 675)
CMD_TEMPLATES = block(681, 735)
CMD_ROLES = block(736, 770)
CMD_DATA = block(771, 804)
RUN_B = block(805, 921)        # cmd_delete, cmd_replan, cmd_compare, cmd_edit
CMD_MODELS = block(922, 947)
CMD_DIAGNOSTICS = block(948, 978)
BUILD_PARSER = block(979, 1194)
MAIN = block(1195, 1221)

# Names every command module may use from the shared context. Generous by
# design: this is a verbatim move, not a rewrite.
SHARED = """from .common import (  # noqa: F401 - shared CLI context (S5b)
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
"""

DOC = '"""Host command module (S5b split); bodies moved verbatim from cli.py."""\n\n'

open("las/app/commands/__init__.py", "w", encoding="utf-8").write(
    '"""Host CLI command modules (S5b): verbatim handlers, shared context."""\n')

open("las/app/commands/common.py", "w", encoding="utf-8").write(
    HEADER + EXITS + HELPERS + NOW)

open("las/app/commands/planning_cmds.py", "w", encoding="utf-8").write(
    DOC + SHARED + CMD_PLAN)

open("las/app/commands/run_cmds.py", "w", encoding="utf-8").write(
    DOC + SHARED + RUN_A + RUN_B + CMD_VERIFY)

open("las/app/commands/provider_cmds.py", "w", encoding="utf-8").write(
    DOC + SHARED + CMD_PROVIDERS + CMD_MODELS)

open("las/app/commands/library_cmds.py", "w", encoding="utf-8").write(
    DOC + SHARED + CMD_TEMPLATES + CMD_ROLES)

open("las/app/commands/data_cmds.py", "w", encoding="utf-8").write(
    DOC + SHARED + CMD_DATA + CMD_DIAGNOSTICS)

open("las/app/commands/system_cmds.py", "w", encoding="utf-8").write(
    DOC + SHARED + CMD_MODULES + CMD_SELFTEST)

CLI = '''"""The ``studio`` command line (spec 7.4).

Exit codes: 0 run succeeded · 1 run failed · 2 invalid plan or arguments ·
3 provider unreachable or authentication failed · 4 waiting at a gate in
non-interactive mode · 5 a cap was reached · 6 run cancelled · 7 internal
error.

S5b: command handlers live in las/app/commands/ (verbatim moves around a
shared context in commands/common.py); this file stays the composition
root - parser, dispatch and the backward-compat import surface.
"""

from __future__ import annotations

import sys

from las.app.commands.common import (  # noqa: F401 - re-exported surface
    EXIT_OK, EXIT_RUN_FAILED, EXIT_INVALID, EXIT_PROVIDER, EXIT_GATE,
    EXIT_CAP, EXIT_CANCELLED, EXIT_INTERNAL,
    default_data_dir, _print_json, _load_plan_arg, _registry_for, _now,
    ModuleDisabledError, ModuleNotInstalledError, PlanFormatError,
    ProviderError, TamperedLogError, message_with_fix,
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


'''

open("las/app/cli.py", "w", encoding="utf-8").write(
    CLI + BUILD_PARSER + MAIN)

print("split written")
