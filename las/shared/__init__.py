"""Local Agent Studio shared kernel (ADR-0001).

Small, stable contracts used by three or more modules. The kernel imports
nothing inside ``las`` except the standard library. Adding a type here
requires: multiple independent consumers, a stable shared concept, and a
narrow contract. Feature business logic never lives here (validation rules
are in ``planquality``, replay logic in ``execution``).

Contracts:
- ``plan_contract``: plan schema v1 types, constants, parsing, variables
- ``provider_contract``: Adapter port, message model, normalized errors,
  provider profiles, fault-injection signal (SimulatedCrash)
- ``events_contract``: event types and the Event record (spec 6.3)
- ``messages``: the user-facing message catalogue (spec section 8)
- ``module_contract``: manifest types for the module registry (ADR-0002)
"""
