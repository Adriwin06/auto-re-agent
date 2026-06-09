"""Agentify Desktop MCP-backed LLM provider.

Drives a browser-based AI session (ChatGPT / Claude / Gemini / Perplexity / Grok
/ AI Studio web UIs) through the local ``agentify-sh/desktop`` MCP server, so the
orchestrator can use the Pro / Advanced subscriptions you are already signed into
instead of metered API tokens.

Mapping to the Agentify ``agentify_query`` tool (verified against the live
server)::

    agentify_query(prompt=..., model=<vendor hint>, key=<stable tab key>,
                   promptPrefix=<system block>)

- ``prompt`` is the only required argument.
- ``model`` is a *vendor hint* (e.g. ``"chatgpt"``, ``"claude"``), taken from the
  re-agent ``model`` config key.  It selects which web UI to drive.
- ``key`` is a stable tab key — **it creates the tab if missing**, so no manual
  GUI setup is needed.  The tab is a real, stateful browser conversation, so this
  provider is **conversational**: one ``key`` per re-agent conversation, with
  follow-up turns sent incrementally to the same tab.
- ``promptPrefix`` carries the system prompt on the first turn of a conversation.

First-run note: the very first query against a vendor opens a browser window for
sign-in / CAPTCHA; Agentify pauses and resumes automatically once you log in.

Requirements: Node.js 20+ (``npx`` launches the server on demand) and the
optional ``mcp`` Python extra (``pip install -e ".[agentify]"``).

Because the protocol is synchronous and the ``mcp`` client is asyncio-based, the
provider runs a private event loop on a dedicated background thread and marshals
each call onto it; the MCP server subprocess is spawned once and kept alive.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from re_agent.llm.protocol import Message, render_messages

logger = logging.getLogger(__name__)

DEFAULT_COMMAND = "npx -y @agentify/desktop mcp"
QUERY_TOOL = "agentify_query"
ENSURE_READY_TOOL = "agentify_ensure_ready"
DEFAULT_KEY_BASE = "re-agent"

# Schema property names we map onto, by preference, if the exact ones are absent.
_PROMPT_ARG_HINTS = ("prompt", "text", "message", "input", "content")
_KEY_ARG_HINTS = ("key", "tab", "session")
_MODEL_ARG_HINTS = ("model", "provider", "vendor")
_PREFIX_ARG_HINTS = ("promptprefix", "prefix", "system")


@dataclass
class _Conversation:
    key: str
    system: str
    primed: bool = False
    history: list[Message] = field(default_factory=list)


class AgentifyProvider:
    """LLM provider backed by the Agentify Desktop MCP server.

    Args:
        model: Vendor hint for the web UI to drive (e.g. ``"chatgpt"``,
            ``"claude"``, ``"gemini"``, ``"perplexity"``, ``"grok"``,
            ``"aistudio"``).  ``None`` lets Agentify use its default/active tab.
        command: Full launch command for the MCP server.  ``None`` uses
            :data:`DEFAULT_COMMAND`.
        timeout_s: Per-call wall-clock timeout for a tool invocation.
        key_base: Prefix for auto-generated stable tab keys.
    """

    def __init__(
        self,
        model: str | None = None,
        command: str | None = None,
        timeout_s: int = 1800,
        key_base: str = DEFAULT_KEY_BASE,
    ) -> None:
        self._model = model
        self._command = (command or DEFAULT_COMMAND).split()
        self._timeout_s = timeout_s
        self._key_base = key_base
        self._conversations: dict[str, _Conversation] = {}

        # Background event loop + persistent MCP session, lazily initialised.
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._session: Any = None
        self._session_cm: Any = None
        self._stdio_cm: Any = None
        self._init_lock = threading.Lock()

        # Resolved tool contract (filled by _resolve_tools).
        self._query_tool: str | None = None
        self._ensure_ready_tool: str | None = None
        self._prompt_arg = "prompt"
        self._key_arg: str | None = "key"
        self._model_arg: str | None = "model"
        self._prefix_arg: str | None = "promptPrefix"

    # -- LLMProvider interface ------------------------------------------------

    @property
    def supports_conversations(self) -> bool:
        # A web tab is a stateful chat; map re-agent conversations onto it.
        return True

    def new_conversation(self, system: str) -> str:
        cid = uuid.uuid4().hex
        key = f"{self._key_base}-{cid[:8]}"
        self._conversations[cid] = _Conversation(key=key, system=system)
        return cid

    def resume(self, conversation_id: str, message: str) -> str:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            raise KeyError(f"Unknown conversation ID: {conversation_id}")

        self._ensure_started()
        prefix = None if conv.primed else (conv.system or None)
        first_turn = not conv.primed
        try:
            text = self._run(
                self._query(prompt=message, key=conv.key, prefix=prefix, ensure_ready=first_turn)
            )
        except Exception as exc:  # noqa: BLE001 — normalise for FallbackProvider
            raise RuntimeError(f"agentify query failed: {exc}") from exc

        conv.primed = True
        conv.history.append(Message(role="user", content=message))
        conv.history.append(Message(role="assistant", content=text))
        return text

    def warm_up(self, prompt: str = "Reply with the single word: ready.") -> str:
        """Open the browser session and wait for sign-in, so later runs don't block.

        Sends one trivial prompt to a dedicated ``<key_base>-login`` tab.  This
        auto-creates the tab, opens the vendor's web UI, and pauses for any
        login / CAPTCHA — exactly the interactive step we want to get out of the
        way *before* an unattended ``reverse`` run.  The vendor login is cached
        in Agentify's local browser profile and reused by later tabs.
        """
        self._ensure_started()
        key = f"{self._key_base}-login"
        try:
            return self._run(self._query(prompt=prompt, key=key, prefix=None, ensure_ready=True))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"agentify warm-up failed: {exc}") from exc

    def send(self, messages: list[Message], **kwargs: Any) -> str:
        """One-shot path for stateless callers: fresh tab, full transcript."""
        self._ensure_started()
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        body = render_messages([m for m in messages if m.role != "system"])
        ephemeral_key = f"{self._key_base}-oneshot-{uuid.uuid4().hex[:8]}"
        try:
            return self._run(
                self._query(
                    prompt=body,
                    key=ephemeral_key,
                    prefix=system or None,
                    ensure_ready=True,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"agentify query failed: {exc}") from exc

    # -- MCP plumbing ---------------------------------------------------------

    async def _query(
        self, prompt: str, key: str, prefix: str | None, ensure_ready: bool
    ) -> str:
        if self._session is None or self._query_tool is None:
            raise RuntimeError("Agentify MCP session is not initialised")

        if ensure_ready and self._ensure_ready_tool is not None:
            ready_args = self._addressing_args(key)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    self._session.call_tool(self._ensure_ready_tool, ready_args),
                    timeout=self._timeout_s,
                )

        arguments: dict[str, Any] = {self._prompt_arg: prompt}
        arguments.update(self._addressing_args(key))
        if prefix and self._prefix_arg:
            arguments[self._prefix_arg] = prefix

        result = await asyncio.wait_for(
            self._session.call_tool(self._query_tool, arguments),
            timeout=self._timeout_s,
        )
        return _extract_text(result)

    def _addressing_args(self, key: str) -> dict[str, Any]:
        """Build the {key, model} addressing arguments the server accepts."""
        args: dict[str, Any] = {}
        if self._key_arg:
            args[self._key_arg] = key
        if self._model_arg and self._model:
            args[self._model_arg] = self._model
        return args

    def _ensure_started(self) -> None:
        if self._session is not None:
            return
        with self._init_lock:
            if self._session is not None:
                return
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="agentify-mcp", daemon=True)
            thread.start()
            self._loop = loop
            self._loop_thread = thread
            self._run(self._connect())

    async def _connect(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as err:
            raise ImportError(
                "The 'mcp' package is required for the 'agentify' provider. "
                "Install it with: pip install -e \".[agentify]\"  (or pip install mcp)"
            ) from err

        params = StdioServerParameters(command=self._command[0], args=self._command[1:])
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        session = await self._session_cm.__aenter__()
        await session.initialize()

        tools = (await session.list_tools()).tools
        self._resolve_tools(tools)
        self._session = session
        logger.info(
            "Agentify MCP session ready; query tool '%s' (prompt=%s, key=%s, model=%s)",
            self._query_tool,
            self._prompt_arg,
            self._key_arg,
            self._model_arg,
        )

    def _resolve_tools(self, tools: list[Any]) -> None:
        """Locate the query/ensure-ready tools and their argument names."""
        by_name = {t.name: t for t in tools}

        query = by_name.get(QUERY_TOOL) or _find_tool(tools, ("query", "prompt", "chat", "ask"))
        if query is None:
            available = ", ".join(t.name for t in tools) or "<none>"
            raise RuntimeError(
                "Agentify MCP server exposed no recognisable query tool "
                f"(looked for '{QUERY_TOOL}'). Tools available: {available}"
            )

        props = _schema_props(query)
        self._query_tool = query.name
        self._prompt_arg = _first_match(props, _PROMPT_ARG_HINTS) or (props[0] if props else "prompt")
        self._key_arg = _first_match(props, _KEY_ARG_HINTS)
        self._model_arg = _first_match(props, _MODEL_ARG_HINTS)
        self._prefix_arg = _first_match(props, _PREFIX_ARG_HINTS)

        ready = by_name.get(ENSURE_READY_TOOL) or _find_tool(tools, ("ensure_ready", "ready"))
        self._ensure_ready_tool = ready.name if ready is not None else None

    def _run(self, coro: Any) -> Any:
        if self._loop is None:
            raise RuntimeError("Agentify event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return

        async def _shutdown() -> None:
            if self._session_cm is not None:
                with contextlib.suppress(Exception):
                    await self._session_cm.__aexit__(None, None, None)
            if self._stdio_cm is not None:
                with contextlib.suppress(Exception):
                    await self._stdio_cm.__aexit__(None, None, None)

        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(timeout=10)
        loop.call_soon_threadsafe(loop.stop)
        self._session = None
        self._loop = None

    def __del__(self) -> None:  # best-effort cleanup
        with contextlib.suppress(Exception):
            self.close()


# -- module helpers -----------------------------------------------------------


def _schema_props(tool: Any) -> list[str]:
    schema = getattr(tool, "inputSchema", None) or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    return list(props.keys())


def _find_tool(tools: list[Any], hints: tuple[str, ...]) -> Any | None:
    for hint in hints:
        for tool in tools:
            if hint in tool.name.lower():
                return tool
    return None


def _first_match(names: list[str], hints: tuple[str, ...]) -> str | None:
    """Return the first name whose lowercase form contains one of the hints."""
    lowered = [(n.lower(), n) for n in names]
    for hint in hints:
        for low, orig in lowered:
            if hint in low:
                return orig
    return None


def _extract_text(result: Any) -> str:
    """Pull plain text out of an MCP ``CallToolResult``."""
    content = getattr(result, "content", None)
    if content is None:
        return str(result)
    parts: list[str] = []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()
