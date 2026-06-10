"""Fix loop — reverser -> checker -> fix, bounded by max rounds."""
from __future__ import annotations

import json
import time
from pathlib import Path

from re_agent.agents.checker import CheckerAgent
from re_agent.agents.reverser import ReverserAgent
from re_agent.backend.protocol import REBackend
from re_agent.config.schema import ProjectProfile
from re_agent.core.models import (
    CheckerVerdict,
    FunctionTarget,
    ObjectiveVerdict,
    ReversalResult,
    Verdict,
)
from re_agent.core.session import Session
from re_agent.llm.protocol import LLMProvider, Message
from re_agent.parity.source_indexer import SourceIndexer
from re_agent.runtime.events import emit_event
from re_agent.verification.objective import verify_candidate


def run_fix_loop(
    target: FunctionTarget,
    backend: REBackend,
    reverser_llm: LLMProvider,
    checker_llm: LLMProvider | None = None,
    max_rounds: int = 4,
    log_dir: Path | None = None,
    source_root: Path | None = None,
    project_profile: ProjectProfile | None = None,
    indexer: SourceIndexer | None = None,
    session: Session | None = None,
    report_dir: Path | None = None,
    objective_verifier_enabled: bool = True,
    objective_call_count_tolerance: int = 3,
    objective_control_flow_tolerance: int = 2,
    ida_bin: str | None = None,
) -> ReversalResult:
    """Run the reverser->checker->fix loop up to max_rounds.

    Args:
        target: Function to reverse
        backend: RE backend for Ghidra data
        reverser_llm: LLM provider for the reverser agent
        checker_llm: LLM provider for the checker agent (defaults to reverser_llm)
        max_rounds: Maximum fix iterations
        log_dir: Directory to write prompt/response logs

    Returns:
        ReversalResult with the final code and verdict
    """
    if checker_llm is None:
        checker_llm = reverser_llm

    reverser = ReverserAgent(
        reverser_llm,
        backend,
        source_root=source_root,
        project_profile=project_profile,
        indexer=indexer,
        session=session,
        report_dir=report_dir,
    )
    checker = CheckerAgent(checker_llm, backend)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    code = ""
    last_verdict: CheckerVerdict | None = None
    last_objective_verdict: ObjectiveVerdict | None = None

    emit_event(
        "fix_loop.started",
        {
            "target": target,
            "max_rounds": max_rounds,
            "objective_verifier_enabled": objective_verifier_enabled,
        },
    )

    for round_num in range(1, max_rounds + 1):
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        phase = "reverse" if round_num == 1 else "fix"
        emit_event(
            "fix_loop.round.started",
            {
                "target": target,
                "round": round_num,
                "phase": phase,
            },
        )

        # Reverse (or fix)
        if round_num == 1:
            code, tag = reverser.reverse(target)
        else:
            assert last_verdict is not None
            code, tag = reverser.fix(
                checker_report=last_verdict.summary,
                issues=last_verdict.issues,
                fix_instructions=last_verdict.fix_instructions,
                target=target,
                objective_findings=last_objective_verdict.findings if last_objective_verdict else None,
            )

        emit_event(
            "reverser.completed",
            {
                "target": target,
                "round": round_num,
                "phase": phase,
                "tag": tag,
                "code": code,
                "code_length": len(code),
            },
        )

        # Intercept cross-build reference request if the LLM asked for it.
        if reverser.last_response and "[REQUEST_CROSS_REF]" in reverser.last_response:
            emit_event("cross_ref.requested", {"target": target, "round": round_num})
            cross_exports = (
                project_profile.cross_ref_exports if project_profile else {}
            )
            base = source_root.parent if source_root else Path(".")
            resolved = {
                label: (Path(d) if Path(d).is_absolute() else base / d)
                for label, d in cross_exports.items()
            }
            from re_agent.backend.cross_ref import CrossRefManager
            refs = CrossRefManager(resolved).lookup(
                target.class_name or "", target.function_name or ""
            )
            if refs:
                emit_event(
                    "cross_ref.completed",
                    {"target": target, "round": round_num, "references": refs},
                )
                msg = (
                    refs + "\n\nUse these cross-build references to resolve inlining "
                    "and confirm behavior, then output the corrected C++ function."
                )
                if reverser._conversation_id:
                    response = reverser.llm.resume(reverser._conversation_id, msg)
                else:
                    response = reverser.llm.send([
                        Message(role="system", content=reverser._system_prompt),
                        Message(role="user", content=reverser.last_prompt),
                        Message(role="assistant", content=reverser.last_response),
                        Message(role="user", content=msg),
                    ])
                reverser.last_response = response
                code = reverser._extract_code(response)
                tag = reverser._extract_tag(response)
                emit_event(
                    "reverser.completed",
                    {
                        "target": target,
                        "round": round_num,
                        "phase": "cross-ref-fix",
                        "tag": tag,
                        "code": code,
                        "code_length": len(code),
                    },
                )
                print("[LOOP] Cross-build references injected and C++ regenerated.")
            else:
                emit_event(
                    "cross_ref.failed",
                    {"target": target, "round": round_num,
                     "error": "No same-named function found in other builds."},
                )
                print("[LOOP] Warning: no cross-build references found. Continuing.")

        # Intercept IDA decompile request if LLM requested it
        if reverser.last_response and "[REQUEST_IDA_DECOMPILE]" in reverser.last_response:
            emit_event(
                "ida_fallback.requested",
                {
                    "target": target,
                    "round": round_num,
                },
            )
            print(f"[LOOP] LLM requested IDA decompilation for 0x{target.address}.")
            from re_agent.backend.ida_fallback import IDAFallbackManager
            mgr = IDAFallbackManager(ida_bin=ida_bin)
            pseudocode = mgr.decompile(target.address)
            if pseudocode:
                emit_event(
                    "ida_fallback.completed",
                    {
                        "target": target,
                        "round": round_num,
                        "pseudocode": pseudocode,
                    },
                )
                msg = (
                    f"Here is the pseudocode from the IDA Pro Hex-Rays decompiler for address 0x{target.address}:\n"
                    f"```cpp\n{pseudocode}\n```\n"
                    "Please rewrite and output the C++ function using this decompile."
                )
                if reverser._conversation_id:
                    response = reverser.llm.resume(reverser._conversation_id, msg)
                else:
                    messages = [
                        Message(role="system", content=reverser.last_prompt),
                        Message(role="user", content=reverser.last_prompt),
                        Message(role="assistant", content=reverser.last_response),
                        Message(role="user", content=msg),
                    ]
                    response = reverser.llm.send(messages)

                reverser.last_response = response
                code = reverser._extract_code(response)
                tag = reverser._extract_tag(response)
                emit_event(
                    "reverser.completed",
                    {
                        "target": target,
                        "round": round_num,
                        "phase": "ida-fix",
                        "tag": tag,
                        "code": code,
                        "code_length": len(code),
                    },
                )
                print("[LOOP] IDA decompile successfully injected and C++ code regenerated.")
            else:
                emit_event(
                    "ida_fallback.failed",
                    {
                        "target": target,
                        "round": round_num,
                        "error": "IDA decompilation failed or was not configured.",
                    },
                )
                print("[LOOP] Warning: IDA decompilation failed or was not configured. Continuing with current code.")

        if log_dir:
            log_entry = {
                "round": round_num,
                "timestamp": timestamp,
                "phase": "reverse" if round_num == 1 else "fix",
                "target": f"{target.class_name}::{target.function_name}",
                "address": target.address,
                "prompt": reverser.last_prompt,
                "response": reverser.last_response,
                "code_length": len(code),
            }
            log_path = log_dir / f"round{round_num}-{timestamp}-reverser.json"
            log_path.write_text(json.dumps(log_entry, indent=2), encoding="utf-8")

        # Check
        emit_event(
            "checker.started",
            {
                "target": target,
                "round": round_num,
            },
        )
        verdict = checker.check(code, target)
        last_verdict = verdict
        emit_event(
            "checker.completed",
            {
                "target": target,
                "round": round_num,
                "verdict": verdict.verdict.value,
                "summary": verdict.summary,
                "issues": verdict.issues,
                "fix_instructions": verdict.fix_instructions,
            },
        )

        objective_verdict: ObjectiveVerdict | None = None
        if objective_verifier_enabled:
            emit_event(
                "objective_verifier.started",
                {
                    "target": target,
                    "round": round_num,
                },
            )
            objective_verdict = verify_candidate(
                code,
                target,
                backend,
                call_count_tolerance=objective_call_count_tolerance,
                control_flow_tolerance=objective_control_flow_tolerance,
            )
            emit_event(
                "objective_verifier.completed",
                {
                    "target": target,
                    "round": round_num,
                    "verdict": objective_verdict.verdict.value,
                    "summary": objective_verdict.summary,
                    "findings": objective_verdict.findings,
                },
            )
        last_objective_verdict = objective_verdict

        if log_dir:
            check_log = {
                "round": round_num,
                "timestamp": timestamp,
                "phase": "check",
                "prompt": checker.last_prompt,
                "response": checker.last_response,
                "verdict": verdict.verdict.value,
                "summary": verdict.summary,
                "issues": verdict.issues,
                "fix_instructions": verdict.fix_instructions,
                "objective_verdict": objective_verdict.verdict.value if objective_verdict else None,
                "objective_summary": objective_verdict.summary if objective_verdict else "",
                "objective_findings": objective_verdict.findings if objective_verdict else [],
            }
            check_path = log_dir / f"round{round_num}-{timestamp}-checker.json"
            check_path.write_text(json.dumps(check_log, indent=2), encoding="utf-8")

        if verdict.verdict == Verdict.PASS and (
            objective_verdict is None or objective_verdict.verdict != Verdict.FAIL
        ):
            result = ReversalResult(
                target=target,
                code=code,
                checker_verdict=verdict,
                objective_verdict=objective_verdict,
                parity_status=None,
                parity_findings=[],
                rounds_used=round_num,
                success=True,
            )
            emit_event("fix_loop.completed", {"target": target, "result": result})
            return result

    # Exhausted all rounds
    result = ReversalResult(
        target=target,
        code=code,
        checker_verdict=last_verdict,
        objective_verdict=last_objective_verdict,
        parity_status=None,
        parity_findings=[],
        rounds_used=max_rounds,
        success=False,
    )
    emit_event("fix_loop.completed", {"target": target, "result": result})
    return result
