"""Antigravity CLI-backed LLM provider.

Invokes Google's ``agy`` CLI in non-interactive mode (``agy -p``).  Antigravity
is the Go-based successor to the Gemini CLI; it authenticates via Google
Sign-In, so no API key is required.
"""
from __future__ import annotations

import uuid
from typing import Any

from re_agent.llm.protocol import Message
from re_agent.utils.process import run_cmd_split


class AntigravityProvider:
    """LLM provider backed by the local ``agy -p`` CLI.

    Args:
        model: Optional model forwarded as ``--model``.  ``None`` lets ``agy``
            use its configured default.
        timeout_s: Maximum wall-clock seconds before the process is killed.
        agy_bin: Name or path of the Antigravity executable.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout_s: int = 1800,
        agy_bin: str = "agy",
        extra_args: list[str] | None = None,
        env: dict | None = None,
    ) -> None:
        self._model = model
        self._timeout_s = timeout_s
        self._agy_bin = agy_bin
        self._extra_args = list(extra_args or [])
        self._env = dict(env or {})
        self._conversations: dict[str, list[Message]] = {}

    def send(self, messages: list[Message], **kwargs: Any) -> str:
        prompt = self._render_messages(messages)
        model = kwargs.get("model", self._model)

        args = [self._agy_bin]
        if model:
            args += ["--model", str(model)]
        args += ["-p", prompt, *self._extra_args]

        returncode, stdout, stderr = run_cmd_split(args, timeout_s=self._timeout_s, env=self._env)
        if returncode != 0:
            raise RuntimeError(
                f"agy -p failed with exit code {returncode}\n{stderr or stdout}"
            )
        if not stdout.strip():
            detail = stderr.strip() or "empty response"
            raise RuntimeError(f"agy -p returned empty response\n{detail}")
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
