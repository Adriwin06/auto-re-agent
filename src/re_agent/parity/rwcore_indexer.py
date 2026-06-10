"""Renderware 4 core (`rw::`) type indexer for reverser context injection.

Loads the offline Ghidra export of `rwcore_master.obj` (the `rw::` namespace from
rwcore.lib + rwcore.pdb) and, given a blob of context text, returns formatted
definitions for any `rw::` struct/enum names mentioned. This is the runtime side
of the rwcore type pass: the generated headers under
`b5-decomp/vendor/renderware/` fix the vocabulary in the source tree; this feeds
the same layouts into the prompt so the LLM names placeholder engine types
correctly instead of re-deriving them.

Graceful no-op when the export is absent (returns "" from ``lookup``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# A qualified rw name (rw::core::debug::Channel) or a bare identifier token.
_QUALIFIED_RE = re.compile(r"\brw(?:::[A-Za-z_]\w*)+\b")
_WORD_RE = re.compile(r"\b[A-Za-z_]\w{2,}\b")

# Short names too generic to match on their own (would inject noise).
_AMBIGUOUS_SHORT = frozenset({
    "Data", "Device", "Channel", "String", "Exception", "Allocator",
})


class RwcoreTypeIndexer:
    """Indexes `rw::` structs/enums from the rwcore Ghidra export."""

    def __init__(self, export_root: Path) -> None:
        self.export_root = export_root
        self.structs: dict[str, dict] = {}
        self.enums: dict[str, dict] = {}
        # short name -> set of full names (for unambiguous short-name matches)
        self._by_short: dict[str, set[str]] = {}

        self._load()

    @property
    def available(self) -> bool:
        return bool(self.structs or self.enums)

    def _load(self) -> None:
        sp = self.export_root / "_structs.json"
        ep = self.export_root / "_enums.json"
        if sp.exists():
            self.structs = {
                n: v for n, v in json.loads(sp.read_text(encoding="utf-8")).items()
                if n.split("::")[0] == "rw"
            }
        if ep.exists():
            self.enums = {
                n: v for n, v in json.loads(ep.read_text(encoding="utf-8")).items()
                if n.split("::")[0] == "rw"
            }
        for full in (*self.structs, *self.enums):
            self._by_short.setdefault(full.split("::")[-1], set()).add(full)

    def lookup(self, *texts: str, max_types: int = 6) -> str:
        """Return formatted `rw::` definitions mentioned across ``texts``.

        Matches full qualified names (``rw::core::debug::Channel``) anywhere, plus
        bare identifiers that resolve to exactly one non-ambiguous `rw::` type.
        Returns "" when the export is missing or nothing matches.
        """
        if not self.available:
            return ""
        blob = "\n".join(t for t in texts if t)
        if not blob:
            return ""

        found: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            if name not in seen and (name in self.structs or name in self.enums):
                seen.add(name)
                found.append(name)

        for m in _QUALIFIED_RE.findall(blob):
            add(m)

        words = set(_WORD_RE.findall(blob))
        for w in words:
            if w in _AMBIGUOUS_SHORT:
                continue
            owners = self._by_short.get(w)
            if owners and len(owners) == 1:
                add(next(iter(owners)))

        if not found:
            return ""

        # Prefer qualified-name hits (already first), cap total.
        blocks = [self._render(n) for n in found[:max_types]]
        note = ("Renderware 4 core types (rw::) -- canonical layouts from rwcore.pdb; "
                "headers in b5-decomp/vendor/renderware/. Use these names for "
                "placeholder/stripped engine types:")
        return note + "\n\n" + "\n\n".join(blocks)

    def _render(self, name: str) -> str:
        if name in self.enums:
            e = self.enums[name]
            vals = "\n".join(f"  {v['name']} = {v['value']}," for v in e["values"])
            return f"enum {name} (size {e.get('size')}):\n{vals}"
        s = self.structs[name]
        lines = [f"struct {name} (size: {s['size']}):"]
        for f in s["fields"]:
            if f["type"] == "undefined":
                continue  # skip Ghidra filler bytes
            lines.append(f"  +0x{f['offset']:X} {f['type']} {f['name']} (size: {f['size']})")
        return "\n".join(lines)
