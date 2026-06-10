"""DecFIGS source-structure indexer — up-front file attribution + inlining.

The DecFIGS Internal PS3 build carries DWARF source-line info. Extracted offline
(tools/ida_export_lineinfo.py -> tools/build_source_tree.py) into
`.ghidra-exports/decfigs/decfigs_func_files.json`:

    addr -> {name, home_file, dominant_file, span_count, inlined_files}

`home_file` is where the function is *defined* (its entry/prologue maps there);
`inlined_files` are the other source files whose code is inlined into its body.
This lets the reverser know, BEFORE writing anything, which file a function
belongs in and which inlined helpers to expect/factor out — addressing inlining
structurally up front instead of reconstructing it later.

DecFIGS names are Itanium-mangled (PS3 GCC); we extract identifier tokens so the
lookup is keyed by class/function name and works against the active oracle
(X360/PS3) by name, no address map. Graceful no-op when the export is absent.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

_LEN = re.compile(r"(\d+)")


def demangle_tokens(name: str) -> list[str]:
    """Extract identifier components from an Itanium-mangled name.

    `._ZN6Attrib8TypeDesc6LookupEy` -> ['Attrib', 'TypeDesc', 'Lookup'].
    Returns [] for names we can't parse (best-effort, no full demangler).
    """
    s = name.lstrip(".")
    if not s.startswith("_Z"):
        return []
    s = s[2:]
    nested = s.startswith("N")
    if nested:
        s = s[1:]
    tokens: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        m = _LEN.match(s, i)
        if not m:
            break
        ln = int(m.group(1))
        start = m.end()
        end = start + ln
        if end > n:
            break
        tokens.append(s[start:end])
        i = end
        if not nested:  # non-nested: single name only
            break
    return tokens


class DecfigsSourceIndexer:
    """Name-keyed lookup of DecFIGS home file + inlined-source attribution."""

    def __init__(self, export_root: Path) -> None:
        self.export_root = export_root
        # method-name -> list of (class_token_set, record)
        self._by_method: dict[str, list[tuple[frozenset[str], dict]]] = defaultdict(list)
        self._load()

    @property
    def available(self) -> bool:
        return bool(self._by_method)

    def _load(self) -> None:
        path = self.export_root / "decfigs_func_files.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for rec in data.values():
            toks = demangle_tokens(rec.get("name") or "")
            if not toks:
                continue
            method = toks[-1]
            classes = frozenset(toks[:-1])
            self._by_method[method].append((classes, rec))

    @staticmethod
    def _pick_definition_file(classes, method, rec) -> tuple[str, list[str]]:
        """Choose the file the function is *defined* in, and the rest as inlined.

        Instruction-position heuristics fail when a function's prologue is itself
        inlined header code (e.g. a destructor that opens with inlined container
        cleanup). The robust signal is the function's own name: pick the candidate
        file whose basename matches a class/method token, preferring a `.cpp`.
        """
        cands = [rec["home_file"]] + list(rec.get("inlined_files") or [])
        toks = [t.lower() for t in (*classes, method) if len(t) >= 4]

        def stem(f: str) -> str:
            return f.rsplit("/", 1)[-1].lower()

        best = None
        for f in cands:
            matches = any(t in stem(f) for t in toks)
            better = best is None or (f.endswith(".cpp") and not best.endswith(".cpp"))
            if matches and better:
                best = f
        home = best or rec["home_file"]
        inlined = [f for f in cands if f != home]
        return home, inlined

    def lookup(self, class_name: str, function_name: str) -> str:
        """Return the up-front source-structure block for a function, or ""."""
        function_name = (function_name or "").strip()
        class_name = (class_name or "").strip()
        if not self.available or not function_name:
            return ""
        cands = self._by_method.get(function_name)
        if not cands:
            return ""
        # Prefer entries whose class tokens include the target class name.
        rec = None
        classes: frozenset[str] = frozenset()
        if class_name:
            for cls, r in cands:
                if class_name in cls:
                    rec, classes = r, cls
                    break
        if rec is None and len(cands) == 1:
            classes, rec = cands[0]  # unambiguous method name
        if rec is None:
            return ""

        home, inl = self._pick_definition_file(classes, function_name, rec)
        lines = [
            "DecFIGS source structure (authoritative DWARF attribution -- reverse the "
            "function INTO this structure):",
            f"- Home file (where it is defined): {home}",
        ]
        if inl:
            lines.append(
                f"- Inlines code from {len(inl)} other file(s) -- expect these as "
                "separate helpers/calls in the leaked/cleaned source; factor them "
                "back out rather than baking them into this body:"
            )
            for f in inl[:12]:
                lines.append(f"    - {f}")
        else:
            lines.append("- No inlining detected: this body is wholly its home file.")
        return "\n".join(lines)
