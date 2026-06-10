"""Tracing wrappers for LLM providers and reverse-engineering backends."""
from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from typing import Any

from re_agent.backend.protocol import REBackend
from re_agent.llm.protocol import LLMProvider, Message
from re_agent.runtime.events import emit_event


class TracedLLMProvider:
    """LLMProvider wrapper that emits prompt, reply, and timing events."""

    def __init__(self, wrapped: LLMProvider, role: str) -> None:
        self.wrapped = wrapped
        self.role = role

    @property
    def supports_conversations(self) -> bool:
        return self.wrapped.supports_conversations

    def send(self, messages: list[Message], **kwargs: Any) -> str:
        started = time.perf_counter()
        emit_event(
            "llm.call.started",
            {
                "role": self.role,
                "provider": type(self.wrapped).__name__,
                "method": "send",
                "messages": [asdict(m) for m in messages],
                "kwargs": kwargs,
            },
        )
        try:
            response = self.wrapped.send(messages, **kwargs)
        except Exception as exc:
            emit_event(
                "llm.call.failed",
                {
                    "role": self.role,
                    "provider": type(self.wrapped).__name__,
                    "method": "send",
                    "duration_s": time.perf_counter() - started,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        emit_event(
            "llm.call.completed",
            {
                "role": self.role,
                "provider": type(self.wrapped).__name__,
                "method": "send",
                "duration_s": time.perf_counter() - started,
                "response": response,
            },
        )
        return response

    def new_conversation(self, system: str) -> str:
        started = time.perf_counter()
        emit_event(
            "llm.conversation.started",
            {
                "role": self.role,
                "provider": type(self.wrapped).__name__,
                "system": system,
            },
        )
        try:
            conversation_id = self.wrapped.new_conversation(system)
        except Exception as exc:
            emit_event(
                "llm.conversation.failed",
                {
                    "role": self.role,
                    "provider": type(self.wrapped).__name__,
                    "duration_s": time.perf_counter() - started,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        emit_event(
            "llm.conversation.ready",
            {
                "role": self.role,
                "provider": type(self.wrapped).__name__,
                "duration_s": time.perf_counter() - started,
                "conversation_id": conversation_id,
            },
        )
        return conversation_id

    def resume(self, conversation_id: str, message: str) -> str:
        started = time.perf_counter()
        emit_event(
            "llm.call.started",
            {
                "role": self.role,
                "provider": type(self.wrapped).__name__,
                "method": "resume",
                "conversation_id": conversation_id,
                "messages": [{"role": "user", "content": message}],
            },
        )
        try:
            response = self.wrapped.resume(conversation_id, message)
        except Exception as exc:
            emit_event(
                "llm.call.failed",
                {
                    "role": self.role,
                    "provider": type(self.wrapped).__name__,
                    "method": "resume",
                    "conversation_id": conversation_id,
                    "duration_s": time.perf_counter() - started,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        emit_event(
            "llm.call.completed",
            {
                "role": self.role,
                "provider": type(self.wrapped).__name__,
                "method": "resume",
                "conversation_id": conversation_id,
                "duration_s": time.perf_counter() - started,
                "response": response,
            },
        )
        return response


class TracedBackend:
    """REBackend wrapper that emits calls and full decompile outputs."""

    def __init__(self, wrapped: REBackend) -> None:
        self.wrapped = wrapped

    @property
    def capabilities(self) -> Any:
        return self.wrapped.capabilities

    def decompile(self, target: str) -> Any:
        return self._call("decompile", target, include_result=True)

    def xrefs_to(self, target: str) -> Any:
        return self._call("xrefs_to", target, include_result=True)

    def xrefs_from(self, target: str) -> Any:
        return self._call("xrefs_from", target, include_result=True)

    def get_struct(self, name: str) -> Any:
        return self._call("get_struct", name, include_result=True)

    def get_enum(self, name: str) -> Any:
        return self._call("get_enum", name, include_result=True)

    def get_asm(self, target: str) -> Any:
        return self._call("get_asm", target, include_result=True)

    def search(self, pattern: str) -> Any:
        return self._call("search", pattern, include_result=True)

    def unimplemented(self, filter_pattern: str | None = None) -> Any:
        return self._call("unimplemented", filter_pattern, include_result=True)

    def remaining(self, class_name: str | None = None) -> Any:
        return self._call("remaining", class_name, include_result=True)

    def _call(self, method: str, argument: str | None, include_result: bool = False) -> Any:
        started = time.perf_counter()
        emit_event(
            "backend.call.started",
            {
                "backend": type(self.wrapped).__name__,
                "method": method,
                "argument": argument,
            },
        )
        try:
            fn = getattr(self.wrapped, method)
            result = fn(argument) if argument is not None else fn()
        except Exception as exc:
            emit_event(
                "backend.call.failed",
                {
                    "method": method,
                    "backend": type(self.wrapped).__name__,
                    "argument": argument,
                    "duration_s": time.perf_counter() - started,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            raise

        payload: dict[str, Any] = {
            "method": method,
            "backend": type(self.wrapped).__name__,
            "argument": argument,
            "duration_s": time.perf_counter() - started,
        }
        if include_result:
            payload["result"] = _result_payload(result)
        emit_event("backend.call.completed", payload)
        if method == "decompile":
            emit_event(
                "backend.decompile.completed",
                {
                    "target": argument,
                    "duration_s": payload["duration_s"],
                    "decompile": _result_payload(result),
                },
            )
        return result


def _result_payload(result: Any) -> Any:
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    if isinstance(result, list):
        return [_result_payload(item) for item in result]
    if isinstance(result, tuple):
        return [_result_payload(item) for item in result]
    if isinstance(result, dict):
        return {str(k): _result_payload(v) for k, v in result.items()}
    return result
