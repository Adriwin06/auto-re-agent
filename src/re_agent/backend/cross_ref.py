"""Cross-build name-keyed decompile lookup (on-demand `[REQUEST_CROSS_REF]`).

When the reverser's single active target is insufficient -- typically because a
function is inlined in the active build but standalone elsewhere, or its logic is
ambiguous -- it can emit ``[REQUEST_CROSS_REF]``. The loop then pulls the
*same-named* function's decompile from the other Ghidra exports (X360, PS3,
DecFIGS, TUB, BPR) and injects them so the agent can triangulate.

No address map is required: MSVC-mangled and demangled symbol names both contain
the class/function identifiers as substrings, so token-substring matching aligns
functions across builds. This is the lazy alternative to a precomputed unified
address map -- we pay the multi-build cost only on the functions that ask for it.

Each export dir holds one ``<address>.json`` per function (with a ``decompiled``
field) plus an ``_index.json`` mapping address -> {name, num_callers, ...}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_IDENT = re.compile(r"[A-Za-z_]\w+")


class CrossRefManager:
    """Resolves a function name to its decompile across multiple build exports."""

    def __init__(
        self,
        exports: dict[str, Path],
        max_per_build: int = 1,
        max_chars_per_body: int = 2500,
    ) -> None:
        # label -> export dir
        self.exports = {k: Path(v) for k, v in exports.items()}
        self.max_per_build = max_per_build
        self.max_chars_per_body = max_chars_per_body
        # label -> list[(address, name)] index, loaded lazily
        self._index_cache: dict[str, list[tuple[str, str]]] = {}

    def _index(self, label: str) -> list[tuple[str, str]]:
        cached = self._index_cache.get(label)
        if cached is not None:
            return cached
        out: list[tuple[str, str]] = []
        idx_path = self.exports[label] / "_index.json"
        if idx_path.exists():
            try:
                data = json.loads(idx_path.read_text(encoding="utf-8"))
                for addr, meta in data.items():
                    name = meta.get("name") if isinstance(meta, dict) else None
                    if name:
                        out.append((addr, name))
            except (OSError, json.JSONDecodeError):
                out = []
        self._index_cache[label] = out
        return out

    def _decompile(self, label: str, address: str) -> str:
        path = self.exports[label] / f"{address}.json"
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        body = data.get("decompiled") or ""
        if len(body) > self.max_chars_per_body:
            body = body[: self.max_chars_per_body].rstrip() + "\n// ...[truncated]"
        return body

    @staticmethod
    def _matches(name: str, tokens: list[str]) -> bool:
        # All required identifier tokens must appear as substrings of the symbol.
        return all(tok in name for tok in tokens)

    def _candidates(self, label: str, tokens: list[str]) -> list[tuple[str, str]]:
        hits = [(addr, name) for addr, name in self._index(label)
                if self._matches(name, tokens)]
        # Tighter matches first (shortest symbol => least decoration/extra context).
        hits.sort(key=lambda an: (len(an[1]), an[1]))
        return hits[: self.max_per_build]

    def lookup(self, class_name: str, function_name: str) -> str:
        """Return formatted same-named decompiles across builds, or "".

        Requires the function name; includes the class name as a second required
        token when present (cuts false positives on common method names).
        """
        function_name = (function_name or "").strip()
        class_name = (class_name or "").strip()
        if not function_name or not _IDENT.fullmatch(function_name):
            return ""
        tokens = [function_name]
        if class_name and _IDENT.fullmatch(class_name):
            tokens.append(class_name)

        blocks: list[str] = []
        for label in self.exports:
            for addr, name in self._candidates(label, tokens):
                body = self._decompile(label, addr)
                if not body.strip():
                    continue
                blocks.append(
                    f"### {label} -- {name} @ {addr}\n```cpp\n{body}\n```"
                )

        if not blocks:
            return ""
        header = (
            f"Cross-build references for {class_name + '::' if class_name else ''}"
            f"{function_name} (same-named function in other builds; use to recover "
            "inlined boundaries and confirm logic -- treat exact bytes as version drift):"
        )
        return header + "\n\n" + "\n\n".join(blocks)
