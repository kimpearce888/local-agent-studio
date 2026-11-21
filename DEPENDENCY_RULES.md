# Dependency Rules

## 1. Allowed dependency direction

The dependency graph is acyclic. Direction of allowed imports:

```
                    ┌───────────────────────────────┐
                    │  shared (kernel contracts)     │
                    │  imports nothing internal      │
                    └──────────────▲────────────────┘
                                   │ (everyone may import shared)
  ┌────────────────┐         ┌─────┴──────────┐
  │ modules/*      │◀────────│  app (host)     │
  │ import other   │         │  composition +  │
  │ modules'       │         │  registry only  │
  │ CONTRACTS only │         └────────────────┘
  └────────────────┘
```

- `shared` imports nothing inside `las` except the standard library.
- `app` (host) imports everything, for composition and registration only.
- A feature module imports: `shared` freely; other modules **only through their
  package facade** (`las.modules.<id>`) or a registered child facade
  (`las.modules.<parent>.<child>`), and **only if declared** in its manifest
  (`requires` or `optional`).
- Undeclared cross-module imports are boundary violations even through a facade.

## 2. Public versus private access

- The public contract of a module is its package `__init__` (explicit `__all__`).
- Private files inside a multi-file module (for example
  `las/modules/providers/openai_compat.py`) must not be imported by other modules.
  Tests of the owning module may import them.
- Names starting with `_` are private in every module.
- Data access follows ownership (section "Data ownership" in `ARCHITECTURE.md`):
  another module reads a module's data only through that module's contract.

## 3. Shared/platform code

| Capability | Owner | Consumers | Why genuinely shared |
|---|---|---|---|
| Plan schema types (Plan, Task, Constraint) | `shared.plan_contract` | planning, planquality, execution, context, handoffs, security, storage, reporting | Stable domain types used by 6+ modules |
| Provider port (Adapter, ChatRequest, Message, StreamEvent, errors, ToolDef, SimulatedCrash) | `shared.provider_contract` | providers (+mock), planning, execution, context, security, selftest | The port both sides of the hexagonal boundary depend on; keeps providers inverted |
| Event types and payload shapes | `shared.events_contract` | execution, storage, reporting | Integration backbone of the event-sourced core |
| Message catalogue | `shared.messages` | all user-facing modules | One catalogue per spec section 8 |
| Module manifest types | `shared.module_contract` | app registry, every manifest | Registration contract between host and modules |

A type may enter `shared/` only when at least three independent modules use it, it is
a stable concept rather than one feature's implementation, and it is a narrow contract.
Everything else stays in the owning module.

## 4. Forbidden dependencies

| Source | Forbidden target | Reason | Automated check |
|---|---|---|---|
| `shared` | anything inside `las` | Kernel purity | `tests/test_architecture.py` (kernel purity rule) |
| `modules/*` | `las.app` | Host is the top of the graph, never a dependency | `tests/test_architecture.py` (host rule) |
| `modules/providers` | `modules/storage`, `modules/execution`, `modules/context` | Adapter layer stays inverted (fixes finding F-01) | manifest declaration + `tests/test_architecture.py` |
| `modules/storage` | any feature module | Persistence depends on contracts only | manifest declaration + `tests/test_architecture.py` |
| any module except `selftest` | `modules.providers.mock` | Test scaffolding must stay out of production paths (fixes F-02) | manifest declaration + `tests/test_architecture.py` |
| any module | another module's private files | Public contract only | facade rule in `tests/test_architecture.py` |
| any module | `las.modules.X` not declared in its manifest | Hidden dependencies | declaration rule in `tests/test_architecture.py` |

## 5. Circular dependency policy

Circular module dependencies are prohibited. The registry test rejects cycles in
declared dependencies; the architecture checker rejects cycles in actual imports. If a
cycle appears, extract the shared concept into `shared/` or reverse the dependency —
never suppress the checker.

## 6. Exceptions

| Exception | Scope | Rationale | Risk/mitigation | Owner | Review condition |
|---|---|---|---|---|---|
| (none) | | | | | |

Any exception needs a row here plus an ADR, with scope, rationale, risk, mitigation,
owner and a review condition. Blanket or permanent exemptions are not granted.
