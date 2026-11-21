"""Module manifest types for the registry (ADR-0002) — shared kernel contract.

The host registry and every module manifest depend on these types, so they
live in the kernel (see DEPENDENCY_RULES.md section 3). Manifests describe
implemented reality: dependencies are declared when the wiring exists, not
speculatively.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CommandSpec:
    """One CLI command contributed by a module.

    ``handler`` names a callable inside the module contract that receives
    the parsed argparse namespace; the host adapter wires it to the parser.
    """

    name: str
    help: str
    handler: str = ""              # dotted name inside the contract module

    def to_obj(self) -> dict:
        return {"name": self.name, "help": self.help, "handler": self.handler}


@dataclass
class ModuleManifest:
    """Declarative description of one module (ADR-0002).

    ``id`` uses dotted form for children (``providers.mock``). ``core``
    marks host-required modules that cannot be disabled. ``requires`` and
    ``optional`` list module ids. ``test_files`` are the test files owned
    by this module (deleted with it by the removal test). ``compat_shims``
    lists transition-era re-export files at legacy paths (ADR-0004).
    """

    id: str
    version: str
    parent: Optional[str] = None
    summary: str = ""
    contract: str = ""                       # import path of the public facade
    core: bool = False
    requires: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    default_enabled: bool = True
    commands: list[CommandSpec] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)
    compat_shims: list[str] = field(default_factory=list)

    def to_obj(self) -> dict:
        return {
            "id": self.id, "version": self.version, "parent": self.parent,
            "summary": self.summary, "contract": self.contract, "core": self.core,
            "requires": list(self.requires), "optional": list(self.optional),
            "default_enabled": self.default_enabled,
            "commands": [c.to_obj() for c in self.commands],
            "test_files": list(self.test_files),
            "compat_shims": list(self.compat_shims),
        }
