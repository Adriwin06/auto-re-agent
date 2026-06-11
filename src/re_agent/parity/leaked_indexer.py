"""Leaked source code parser and indexer for Burnout Paradise decompilation reference context."""
from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

FUNC_TOKEN_RE = re.compile(r"\b([A-Za-z_~][A-Za-z0-9_]*)::([A-Za-z_~][A-Za-z0-9_]*)\s*\(")
CLASS_DECL_RE = re.compile(r"\b(class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)\b")


class LeakedSourceIndexer:
    """Indexes split leaked source files (class declarations and function definitions)."""

    def __init__(self, leaked_root: Path) -> None:
        self.leaked_root = leaked_root
        self.source_files: list[Path] = []
        if leaked_root.exists():
            self.source_files = sorted(
                p for ext in (".cpp", ".h", ".hpp", ".hh")
                for p in leaked_root.rglob(f"*{ext}")
            )

        self.file_text_cache: dict[Path, str] = {}
        # Maps (class_name, fn_name) -> list of (path, offset). Holds *every*
        # ``Class::method(`` occurrence, including bare call sites.
        self.token_index: dict[tuple[str, str], list[tuple[Path, int]]] = defaultdict(list)
        # Subset of token_index restricted to actual *definitions* (occurrences
        # backed by a ``{ ... }`` body). Call sites are excluded, so this is the
        # only safe basis for locating a class's home directory — a class merely
        # *called* from some file must not be filed under that caller's folder.
        self.definition_index: dict[tuple[str, str], list[tuple[Path, int]]] = defaultdict(list)
        # Maps class_name -> list of (path, offset)
        self.class_index: dict[str, list[tuple[Path, int]]] = defaultdict(list)

        self.function_cache: dict[tuple[str, str], str | None] = {}
        self.class_cache: dict[str, str | None] = {}
        self._known_class_names: frozenset[str] | None = None

        self._build_index()

    def known_class_names(self) -> frozenset[str]:
        """All class names known to the leaked source — from both class/struct
        declarations and ``Class::method`` definition tokens. Cached."""
        if self._known_class_names is None:
            names = set(self.class_index)
            names.update(cls for cls, _fn in self.token_index)
            self._known_class_names = frozenset(names)
        return self._known_class_names

    def _read_text(self, path: Path) -> str:
        txt = self.file_text_cache.get(path)
        if txt is None:
            txt = path.read_text(encoding="utf-8", errors="ignore")
            self.file_text_cache[path] = txt
        return txt

    def _build_index(self) -> None:
        for path in self.source_files:
            txt = self._read_text(path)
            # Find function tokens (Class::Method)
            for m in FUNC_TOKEN_RE.finditer(txt):
                cls, fn = m.group(1), m.group(2)
                self.token_index[(cls, fn)].append((path, m.start()))
                # A real definition is followed by a ``{ ... }`` body; a call
                # site is not. Record only definitions so placement logic never
                # mistakes a caller's directory for the class's home.
                fn_start = m.start() + len(cls) + 2  # skip "Class::"
                if self._find_function_body_open(txt, fn_start, fn) is not None:
                    self.definition_index[(cls, fn)].append((path, m.start()))

            # Find class declarations (class ClassName)
            for m in CLASS_DECL_RE.finditer(txt):
                self.class_index[m.group(2)].append((path, m.start()))

    @staticmethod
    def _find_matching_brace(text: str, open_brace_idx: int) -> int | None:
        depth = 0
        in_str = False
        str_quote = ""
        in_sl_comment = False
        in_ml_comment = False
        escaped = False
        i = open_brace_idx
        n = len(text)
        while i < n:
            ch = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if in_sl_comment:
                if ch == "\n":
                    in_sl_comment = False
                i += 1
                continue
            if in_ml_comment:
                if ch == "*" and nxt == "/":
                    in_ml_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == str_quote:
                    in_str = False
                i += 1
                continue
            if ch == "/" and nxt == "/":
                in_sl_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_ml_comment = True
                i += 2
                continue
            if ch in ("'", '"'):
                in_str = True
                str_quote = ch
                i += 1
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    @staticmethod
    def _find_matching_paren(text: str, open_paren_idx: int) -> int | None:
        depth = 0
        in_str = False
        str_quote = ""
        in_sl_comment = False
        in_ml_comment = False
        escaped = False
        i = open_paren_idx
        n = len(text)
        while i < n:
            ch = text[i]
            nxt = text[i + 1] if i + 1 < n else ""
            if in_sl_comment:
                if ch == "\n":
                    in_sl_comment = False
                i += 1
                continue
            if in_ml_comment:
                if ch == "*" and nxt == "/":
                    in_ml_comment = False
                    i += 2
                    continue
                i += 1
                continue
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == str_quote:
                    in_str = False
                i += 1
                continue
            if ch == "/" and nxt == "/":
                in_sl_comment = True
                i += 2
                continue
            if ch == "/" and nxt == "*":
                in_ml_comment = True
                i += 2
                continue
            if ch in ("'", '"'):
                in_str = True
                str_quote = ch
                i += 1
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return None

    @staticmethod
    def _skip_ws(text: str, idx: int) -> int:
        n = len(text)
        while idx < n and text[idx].isspace():
            idx += 1
        return idx

    def _find_function_body_open(self, txt: str, fn_idx: int, fn_name: str) -> int | None:
        paren_open = self._skip_ws(txt, fn_idx + len(fn_name))
        if paren_open >= len(txt) or txt[paren_open] != "(":
            return None
        paren_close = self._find_matching_paren(txt, paren_open)
        if paren_close is None:
            return None
        k = self._skip_ws(txt, paren_close + 1)
        # Skip const/override/final qualifiers
        while True:
            if txt.startswith("const", k):
                k = self._skip_ws(txt, k + 5)
                continue
            if txt.startswith("override", k):
                k = self._skip_ws(txt, k + 8)
                continue
            if txt.startswith("final", k):
                k = self._skip_ws(txt, k + 5)
                continue
            break
        # Skip initializer list
        if k < len(txt) and txt[k] == ":":
            i = k + 1
            n = len(txt)
            while i < n:
                if txt[i] == "{":
                    return i
                elif txt[i] == ";":
                    return None
                i += 1
            return None
        if k < len(txt) and txt[k] == "{":
            return k
        return None

    def find_function(self, class_name: str, function_name: str) -> str | None:
        """Find the leaked function body. Returns full string including signature."""
        if not class_name or not function_name:
            return None
        key = (class_name, function_name)
        if key in self.function_cache:
            return self.function_cache[key]

        candidates = self.token_index.get(key, [])
        for path, idx in candidates:
            txt = self._read_text(path)
            fn_start = idx + len(class_name) + 2
            open_brace = self._find_function_body_open(txt, fn_start, function_name)
            if open_brace is None:
                continue
            close_brace = self._find_matching_brace(txt, open_brace)
            if close_brace is None:
                continue

            # Return signature + body
            # Find the start of the line containing the class prefix
            line_start = txt.rfind("\n", 0, idx) + 1
            body = txt[line_start:close_brace + 1].strip()
            self.function_cache[key] = body
            return body

        self.function_cache[key] = None
        return None

    def find_class_definition(self, class_name: str) -> str | None:
        """Find the class or struct definition. Returns the full struct/class block."""
        if not class_name:
            return None
        if class_name in self.class_cache:
            return self.class_cache[class_name]

        candidates = self.class_index.get(class_name, [])
        for path, idx in candidates:
            txt = self._read_text(path)
            # Find open brace after the declaration, ensuring we don't cross a semicolon (forward declaration)
            k = idx
            n = len(txt)
            open_brace = None
            while k < n:
                ch = txt[k]
                if ch == ";":
                    # Forward declaration: skip
                    break
                if ch == "{":
                    open_brace = k
                    break
                k += 1

            if open_brace is None:
                continue

            close_brace = self._find_matching_brace(txt, open_brace)
            if close_brace is None:
                continue

            # Find trailing semicolon after close brace
            semi = self._skip_ws(txt, close_brace + 1)
            end_idx = semi + 1 if semi < n and txt[semi] == ";" else close_brace + 1

            class_body = txt[idx:end_idx].strip()
            self.class_cache[class_name] = class_body
            return class_body

        self.class_cache[class_name] = None
        return None
