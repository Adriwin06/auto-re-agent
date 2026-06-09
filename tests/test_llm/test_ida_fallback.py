"""Tests for the loop integration of IDA decompile requests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from re_agent.agents.loop import run_fix_loop
from re_agent.core.models import FunctionTarget, Verdict
from re_agent.llm.protocol import LLMProvider, Message


class MockLLM(LLMProvider):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[Message] | tuple[str, str]] = []
        self.supports_conversations_val = True

    def send(self, messages: list[Message], **kwargs: any) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)

    @property
    def supports_conversations(self) -> bool:
        return self.supports_conversations_val

    def new_conversation(self, system: str) -> str:
        return "conv_123"

    def resume(self, conversation_id: str, message: str) -> str:
        self.calls.append((conversation_id, message))
        return self.responses.pop(0)


def test_loop_intercepts_request_and_requests_decompile() -> None:
    # First response: LLM requests IDA decompile
    # Second response: LLM outputs C++ code
    llm = MockLLM([
        "I don't like Ghidra decompile. [REQUEST_IDA_DECOMPILE]",
        "```cpp\nvoid CPhysics::Update() {}\n```\nREVERSED_FUNCTION: CPhysics::Update"
    ])

    backend = MagicMock()
    # Capabilities return defaults
    backend.capabilities.has_decompile = True
    backend.capabilities.has_xrefs = False
    backend.capabilities.has_structs = False
    
    # Mock Ghidra decompile output
    decompile_mock = MagicMock()
    decompile_mock.raw_output = "void GhidraFunc();"
    backend.decompile.return_value = decompile_mock

    # Mock Checker to pass immediately
    verdict_mock = MagicMock()
    verdict_mock.verdict = Verdict.PASS
    verdict_mock.summary = "Pass"
    verdict_mock.issues = []
    verdict_mock.fix_instructions = []

    checker_mock = MagicMock()
    checker_mock.check.return_value = verdict_mock

    target = FunctionTarget(address="0x123", class_name="CPhysics", function_name="Update")

    with patch("re_agent.agents.loop.CheckerAgent", return_value=checker_mock), \
         patch("re_agent.backend.ida_fallback.IDAFallbackManager.decompile", return_value="void CPhysics::Update() { /* IDA */ }") as mock_decompile:
        
        result = run_fix_loop(
            target=target,
            backend=backend,
            reverser_llm=llm,
            checker_llm=llm,
            max_rounds=1,
            objective_verifier_enabled=False
        )

        # Ensure IDA fallback was triggered for the target address
        mock_decompile.assert_called_once_with("0x123")

        # Ensure LLM was resumed with the fallback decompile
        assert len(llm.calls) == 2
        assert isinstance(llm.calls[1], tuple)
        assert llm.calls[1][0] == "conv_123"
        assert "void CPhysics::Update() { /* IDA */ }" in llm.calls[1][1]

        # Verify loop succeeded
        assert result.success
        assert result.code == "void CPhysics::Update() {}"
