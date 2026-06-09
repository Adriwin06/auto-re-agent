"""Claude Code CLI-backed LLM provider.

Invokes the local ``claude`` CLI in non-interactive mode (``claude --print``).
Claude Code uses its own authentication (``~/.claude/``), runs its own tool-use
loop, and can read/grep source files directly — so no API key is required.
"""
from __future__ import annotations

import uuid
from typing import Any

from re_agent.llm.protocol import Message
from re_agent.utils.process import run_cmd_split


class ClaudeCodeProvider:
    """LLM provider backed by the local ``claude --print`` CLI.

    Args:
        model: Optional model override forwarded as ``--model``.  If ``None``,
            the CLI uses its configured default.
        timeout_s: Maximum wall-clock seconds before the process is killed.
        claude_bin: Name or path of the Claude Code executable.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout_s: int = 1800,
        claude_bin: str = "claude",
    ) -> None:
        self._model = model
        self._timeout_s = timeout_s
        self._claude_bin = claude_bin
        self._conversations: dict[str, list[Message]] = {}

    def send(self, messages: list[Message], **kwargs: Any) -> str:
        prompt = self._render_messages(messages)
        model = kwargs.get("model", self._model)

        args = [self._claude_bin, "--print", prompt]
        if model:
            args += ["--model", str(model)]

        returncode, stdout, stderr = run_cmd_split(args, timeout_s=self._timeout_s)
        if returncode != 0:
            raise RuntimeError(
                f"claude --print failed with exit code {returncode}\n{stderr or stdout}"
            )
        return stdout

    @property
    def supports_conversations(self) -> bool:
        return True

    def new_conversation(self, system: str) -> str:
        cid = uuid.uuid4().hex
        self._conversations[cid] = [Message(role="system", content=system)]
        return cid

    def resume(self, conversation_id: str, message: str) -> str:
        history = self._conversations.get(conversation_id)
        if history is None:
            raise KeyError(f"Unknown conversation ID: {conversation_id}")

        history.append(Message(role="user", content=message))
        response_text = self.send(list(history))
        history.append(Message(role="assistant", content=response_text))
        return response_text

    @staticmethod
    def _render_messages(messages: list[Message]) -> str:
        parts: list[str] = []
        for msg in messages:
            role = msg.role.upper()
            parts.append(f"[{role}]\n{msg.content.strip()}")
        return "\n\n".join(parts).strip()
