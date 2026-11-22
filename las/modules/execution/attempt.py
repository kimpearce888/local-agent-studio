"""One task attempt: turns, tools, caps, loop detection, handoff
validation and streaming (spec 5.5, 5.7, RUN-7).

Private implementation file of the execution module. ``d`` is the driving
RunDriver (duck-typed), so this file imports nothing from ``.driver``.
"""

from __future__ import annotations

import hashlib
import json
import secrets

from las.modules.context import (
    TEXT_TOOL_PROTOCOL, estimate_tokens, parse_text_tool_call,
)
from las.shared.provider_contract import (
    Adapter, ChatRequest, Message, ProviderError, SimulatedCrash, ToolCall,
)
from las.modules.execution.budgets import (
    AttemptOutcome, CancelRequested, DEFAULTS, PauseRequested, StallError,
)
from las.modules.handoffs import EvidenceContext, extract_handoff, validate_handoff
from las.modules.storage import canonical_json
from las.shared.plan_contract import Task


def run_attempt(d, task: Task, attempt_no: int, temperature: float) -> AttemptOutcome:
    st = d.state
    limits = task.limits or {}
    max_turns = int(limits.get("max_turns", DEFAULTS["max_turns"]))
    max_tool_calls = int(limits.get("max_tool_calls", DEFAULTS["max_tool_calls"]))
    timeout = float(limits.get("timeout_seconds", DEFAULTS["wall_task_seconds"]))
    max_output = int((task.params or {}).get("max_tokens", DEFAULTS["max_output_tokens"]))
    attempt_id = f"att{attempt_no:03d}-{secrets.token_hex(3)}"

    # Context assembly with the budget ladder (spec 5.4).  Overflow never
    # starts the attempt (RUN-9).
    ctx = d._assemble(task)
    if ctx.overflow:
        return AttemptOutcome(ok=False, code="RUN-CONTEXT-OVERFLOW", kind="terminal")
    for step in ctx.compaction_steps:
        d.store.append(d.run_id, "compaction.applied", {
            "task_id": task.id, "ladder_step": step,
            "tokens_before": ctx.token_estimate, "tokens_after": ctx.token_estimate})

    provider_id, model_id, adapter = d._resolve(task)
    params = dict(task.params or {})
    params.setdefault("temperature", temperature)
    params.setdefault("max_tokens", max_output)

    messages = list(ctx.messages)
    broker = d._broker()
    native_tools = d.config.tool_calling
    tools = broker.tool_defs(task) if native_tools else []
    if not native_tools:
        messages[0] = Message("system", messages[0].content + "\n\n" + TEXT_TOOL_PROTOCOL)

    ts_state = st.task_state(task.id)
    if ts_state is None or ts_state.state not in ("ready", "running"):
        d.store.append(d.run_id, "task.ready", {"task_id": task.id})
    request_hash = hashlib.sha256(canonical_json(
        [m.to_obj() for m in messages] + [t.name for t in tools]
        + [params, model_id]).encode()).hexdigest()
    d.store.append(d.run_id, "attempt.started", {
        "task_id": task.id, "attempt_id": attempt_id, "provider": provider_id,
        "model": model_id, "params": params, "request_hash": request_hash})
    d.state_refetch()
    d.store.write_attempt_file(d.run_id, task.id, attempt_id, "request.json",
                                  json.dumps({"model": model_id, "params": params,
                                              "messages": [m.to_obj() for m in messages],
                                              "tools": [t.name for t in tools]},
                                             ensure_ascii=False, indent=1))

    attempt_start = d.config.clock()
    messages_path = "messages.jsonl"
    tokens_out_total = 0
    tokens_in_total = ctx.token_estimate
    tool_calls_made = 0
    denial_count = 0
    consecutive_tool: list[tuple[str, str]] = []
    assistant_texts: list[str] = []
    tool_names_called: set[str] = set()
    repaired_tool_call = False
    repaired_handoff = False
    no_block_nudges = 0

    def log_message(message: Message, extra_tokens: int = 0):
        entry = {"ts": _now_iso(), "role": message.role, "content": message.content,
                 "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments}
                                for c in message.tool_calls],
                 "tool_call_id": message.tool_call_id, "untrusted": message.untrusted}
        d.store.write_attempt_file(d.run_id, task.id, attempt_id, messages_path,
                                      json.dumps(entry, ensure_ascii=False) + "\n",
                                      append=True)
        d.store.append(d.run_id, "message.added", {
            "task_id": task.id, "attempt_id": attempt_id, "role": message.role,
            "ref": f"attempts/{task.id}/{attempt_id}/{messages_path}",
            "tokens": extra_tokens})

    turn = 0
    while True:
        turn += 1
        if turn > max_turns:
            return AttemptOutcome(False, "CAP-REACHED", "terminal",
                                  attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)
        if d.config.clock() - attempt_start > timeout:
            return AttemptOutcome(False, "TIMEOUT", "stall", attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)
        if st.tokens_used() + tokens_in_total + tokens_out_total >= d.config.cap_tokens_run:
            return AttemptOutcome(False, "CAP-REACHED", "terminal", attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)

        request = ChatRequest(model=model_id, messages=messages, tools=tools,
                              params=dict(params))
        try:
            text, reasoning, tool_calls, usage = stream(d, 
                adapter, request, task, attempt_id)
        except PauseRequested:
            abort_attempt(d, task, attempt_id)
            d.store.append(d.run_id, "run.paused", {"reason": "pause requested"})
            d.state_refetch()
            return AttemptOutcome(False, "paused", "control", attempt_id=attempt_id)
        except CancelRequested:
            abort_attempt(d, task, attempt_id)
            d.store.append(d.run_id, "run.cancelled", {"reason": "user"})
            d.state_refetch()
            return AttemptOutcome(False, "cancelled", "control", attempt_id=attempt_id)
        except SimulatedCrash:
            raise                      # fault injection must propagate (AC-06)
        except StallError:
            return AttemptOutcome(False, "STALL", "stall", attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)
        except ProviderError as exc:
            return AttemptOutcome(False, exc.code, "provider_error", attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)

        if usage is not None:
            tokens_in_total = max(tokens_in_total, usage.tokens_in)
            tokens_out_total += usage.tokens_out
        else:
            tokens_out_total += estimate_tokens(text)

        # Run-level token cap: at most one model call beyond the cap (AC-07).
        if st.tokens_used() + tokens_in_total + tokens_out_total > d.config.cap_tokens_run:
            return AttemptOutcome(False, "CAP-REACHED", "terminal", attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)

        # Text tool protocol (TLS-3): calls arrive inside the reply.
        if not native_tools and not tool_calls:
            parsed = parse_text_tool_call(text)
            if parsed is not None:
                tool_calls = [ToolCall(f"call_{turn}", parsed[0],
                                       parsed[1] if isinstance(parsed[1], str)
                                       else json.dumps(parsed[1]))]
            elif "<tool_call>" in text:
                if repaired_tool_call:
                    return AttemptOutcome(False, "TOOL-CALL-INVALID", "terminal",
                                          attempt_id=attempt_id,
                                          tokens_in=tokens_in_total,
                                          tokens_out=tokens_out_total)
                repaired_tool_call = True
                messages.append(Message("assistant", text))
                log_message(Message("assistant", text))
                messages.append(Message(
                    "user", "Your <tool_call> block could not be read. Reply again with "
                            "exactly one valid <tool_call>{\"name\": ..., \"arguments\": "
                            "{...}}</tool_call> line."))
                continue

        assistant = Message("assistant", text, tool_calls=list(tool_calls))
        messages.append(assistant)
        log_message(assistant, estimate_tokens(text))
        d.store.write_attempt_file(d.run_id, task.id, attempt_id, "final.txt.turn",
                                      text)
        assistant_texts.append(text.strip())

        # Loop detection (spec 5.7): four identical non-empty assistant
        # messages.  Tool-call-only turns have empty text and are normal.
        if (len(assistant_texts) >= 4 and assistant_texts[-1]
                and len(set(assistant_texts[-4:])) == 1):
            return AttemptOutcome(False, "LOOP-DETECTED", "loop", attempt_id=attempt_id,
                                  tokens_in=tokens_in_total, tokens_out=tokens_out_total)

        if tool_calls:
            for tc in tool_calls:
                tool_calls_made += 1
                if tool_calls_made > max_tool_calls:
                    return AttemptOutcome(False, "CAP-REACHED", "terminal",
                                          attempt_id=attempt_id,
                                          tokens_in=tokens_in_total,
                                          tokens_out=tokens_out_total)
                args, parse_error = _parse_tool_args(tc.arguments)
                if parse_error:
                    if repaired_tool_call:
                        return AttemptOutcome(False, "TOOL-CALL-INVALID", "terminal",
                                              attempt_id=attempt_id,
                                              tokens_in=tokens_in_total,
                                              tokens_out=tokens_out_total)
                    repaired_tool_call = True
                    messages.append(Message(
                        "user", f"The arguments of tool {tc.name} could not be read as a "
                                "JSON object. Call it again with valid JSON arguments."))
                    break
                # Loop detection: the same tool call three times in a row.
                consecutive_tool.append((tc.name, tc.arguments))
                if (len(consecutive_tool) >= 3
                        and len(set(consecutive_tool[-3:])) == 1):
                    return AttemptOutcome(False, "LOOP-DETECTED", "loop",
                                          attempt_id=attempt_id,
                                          tokens_in=tokens_in_total,
                                          tokens_out=tokens_out_total)
                decision = broker.execute(tc.name, args, task)
                if decision.allowed:
                    tool_names_called.add(tc.name)
                    d.store.append(d.run_id, "tool.called", {
                        "task_id": task.id, "attempt_id": attempt_id, "tool": tc.name,
                        "arguments_hash": hashlib.sha256(
                            canonical_json(args).encode()).hexdigest(),
                        "result": decision.result[:2000], "duration_ms": 0})
                    result_text = decision.result
                else:
                    denial_count += 1
                    d.store.append(d.run_id, "tool.denied", {
                        "task_id": task.id, "attempt_id": attempt_id, "tool": tc.name,
                        "rule": decision.rule})
                    if denial_count >= 2:
                        return AttemptOutcome(False, "PERMISSION-DENIED", "terminal",
                                              attempt_id=attempt_id,
                                              tokens_in=tokens_in_total,
                                              tokens_out=tokens_out_total)
                    result_text = (f"DENIED ({decision.rule}): the tool {tc.name} is not "
                                   "available for this task. Do not call it again.")
                result_msg = Message("tool", result_text, tool_call_id=tc.id,
                                     name=tc.name, untrusted=True)
                messages.append(result_msg)
                log_message(result_msg)
            else:
                continue          # all tool calls handled -> next turn
            continue              # after an argument repair -> next turn

        # Final message: validate the handoff (spec 5.5).
        d.store.write_attempt_file(d.run_id, task.id, attempt_id, "final.txt", text)
        handoff = extract_handoff(text)
        evidence = EvidenceContext(
            attempt_tool_calls=tool_names_called,
            succeeded_tasks={tid for tid, t in st.tasks.items() if t.state == "succeeded"},
            user_approvals=set(st.approved_gates))
        issues = validate_handoff(handoff, task, st.plan, d._workspace(), evidence)
        if issues:
            # A reply with no <handoff> block at all is not a failed
            # handoff - it is a missing one.  Nudging for it has its own
            # small budget so it never consumes the single repair round
            # a real handoff may need (found by the reference-model
            # benchmark: the model's reasoning turn burned the repair,
            # then one dropped period killed the run).
            if handoff is None and no_block_nudges < 2:
                no_block_nudges += 1
                messages.append(Message(
                    "user",
                    "That message had no <handoff> block, so it cannot be "
                    "recorded as the task result. Reply again with your final "
                    "message ending with the <handoff> JSON block."))
                log_message(messages[-1])
                continue
            if repaired_handoff:
                return AttemptOutcome(False, "HANDOFF-INVALID", "terminal",
                                      attempt_id=attempt_id, final_text=text,
                                      tokens_in=tokens_in_total,
                                      tokens_out=tokens_out_total)
            repaired_handoff = True
            rules = ", ".join(sorted({i.rule for i in issues}))
            messages.append(Message(
                "user",
                "Your handoff failed validation (" + rules + "): "
                + " ".join(i.message for i in issues)
                + " Reply with the corrected final message ending with the <handoff> "
                  "block. Copy binding constraints verbatim."))
            log_message(messages[-1])
            continue
        return AttemptOutcome(True, "", "ok", attempt_id=attempt_id, handoff=handoff,
                              final_text=text, tokens_in=tokens_in_total,
                              tokens_out=tokens_out_total)


def abort_attempt(d, task: Task, attempt_id: str) -> None:
    d.store.append(d.run_id, "attempt.aborted", {
        "task_id": task.id, "attempt_id": attempt_id, "reason": "user"})
    d.store.append(d.run_id, "task.ready", {"task_id": task.id})
    d.state_refetch()

# -- internals: streaming ---------------------------------------------------


def stream(d, adapter: Adapter, request: ChatRequest, task: Task, attempt_id: str):
    text = ""
    reasoning = ""
    tool_calls: list[ToolCall] = []
    usage = None
    last_event = d.config.clock()
    stream_log: list[str] = []
    try:
        for ev in adapter.chat(request):
            if d._cancel:
                raise CancelRequested()
            if d._pause_now:
                raise PauseRequested()
            now = d.config.clock()
            if now - last_event > d.config.stall_timeout:
                raise StallError(f"no tokens for {d.config.stall_timeout}s")
            last_event = now
            stream_log.append(json.dumps(
                {"kind": ev.kind, "text": ev.text,
                 "tool": ev.tool_call.name if ev.tool_call else None},
                ensure_ascii=False))
            if ev.kind == "text_delta":
                text += ev.text
            elif ev.kind == "reasoning_delta":
                reasoning += ev.text
            elif ev.kind == "tool_call" and ev.tool_call is not None:
                tool_calls.append(ev.tool_call)
            elif ev.kind == "usage" and ev.usage is not None:
                usage = ev.usage
    finally:
        if stream_log:
            d.store.write_attempt_file(
                d.run_id, task.id, attempt_id, "stream.log",
                "\n".join(stream_log) + "\n")
    if reasoning:
        d.store.write_attempt_file(d.run_id, task.id, attempt_id, "reasoning.txt",
                                      reasoning)
    return text, reasoning, tool_calls, usage

# -- internals: helpers --------------------------------------------------------


def _parse_tool_args(raw: str) -> tuple[dict, bool]:
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(obj, dict):
            return obj, False
    except (json.JSONDecodeError, ValueError):
        pass
    return {}, True


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
