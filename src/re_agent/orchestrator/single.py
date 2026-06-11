"""Single function reversal pipeline."""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

from re_agent.agents.loop import run_fix_loop
from re_agent.backend.protocol import REBackend
from re_agent.config.schema import ReAgentConfig
from re_agent.core.models import FunctionTarget, HookEntry, ParityStatus, ReversalResult, Verdict
from re_agent.core.session import Session
from re_agent.llm.protocol import LLMProvider
from re_agent.parity.engine import fetch_ghidra_data, score_single
from re_agent.parity.source_indexer import SourceIndexer
from re_agent.runtime.events import emit_event

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# b5-decomp integration helpers
# ---------------------------------------------------------------------------

# single.py lives at: <project>/auto-re-agent/src/re_agent/orchestrator/single.py
#   parents[0] = orchestrator/
#   parents[1] = re_agent/
#   parents[2] = src/
#   parents[3] = auto-re-agent/
#   parents[4] = <project root>  (Burnout-Paradise-Decomp_Workflow)
_PROJECT_ROOT = Path(__file__).parents[4]
_B5_SRC = _PROJECT_ROOT / "b5-decomp" / "src"
_CMAKE = _B5_SRC / "CMakeLists.txt"

# Markers that bracket the auto-managed PRIVATE sources block in CMakeLists.txt.
_AUTO_BEGIN = "# AUTO-BEGIN re-agent"
_AUTO_END   = "# AUTO-END re-agent"

# Source extensions that hold *implementations* (preferred placement target) vs.
# declarations only.
_IMPL_SUFFIXES = (".cpp", ".cc", ".cxx")


@lru_cache(maxsize=4)
def _get_leaked_indexer(leaked_root_str: str | None):
    """Lazily build & cache a LeakedSourceIndexer for the configured leaked tree.

    Returns ``None`` when no leaked source is configured or the path is missing,
    so callers transparently fall back to the heuristic mapping.
    """
    if not leaked_root_str:
        return None
    from re_agent.parity.leaked_indexer import LeakedSourceIndexer

    leaked_path = Path(leaked_root_str)
    if not leaked_path.is_absolute():
        leaked_path = _PROJECT_ROOT / leaked_path
    if not leaked_path.exists():
        logger.warning("leaked_source_root not found, using heuristic paths: %s", leaked_path)
        return None
    return LeakedSourceIndexer(leaked_path)


# A prefix-stripped match (e.g. BrnFoo -> Foo) is only trusted when its method
# implementations concentrate in one directory by at least this fraction; generic
# residuals like "Module"/"Assert" scatter across the tree and are rejected.
_STRIP_DOMINANCE = 0.6


def _resolve_leaked_name(indexer, class_name: str) -> tuple[str, bool] | None:
    """Map the (often prefixed) binary class name to the name used in the leaked
    source, returning ``(name, is_exact)``. IDA/X360 symbols carry ``Brn``/``Cgs``
    prefixes the leaked source usually omits (``BrnBoostManager`` -> ``BoostManager``),
    so try the exact name first, then the prefix-stripped form.
    """
    known = indexer.known_class_names()
    if class_name in known:
        return class_name, True
    for prefix in ("Brn", "Cgs"):
        if class_name.startswith(prefix):
            stripped = class_name[len(prefix):]
            if stripped and stripped in known:
                return stripped, False
    return None


def _leaked_relative_dir(indexer, class_name: str, fn_name: str) -> Path | None:
    """Find the original source directory (relative to the leaked tree root) that
    owns ``class_name`` — preferring the .cpp where the method is implemented, so
    the recovered file lands in the real engine folder layout.

    Exact name matches trust both implementation and declaration sites. Prefix-
    stripped matches trust only a *dominant* implementation directory, to avoid
    routing on over-generic residual names.
    """
    from collections import Counter

    resolved = _resolve_leaked_name(indexer, class_name)
    if resolved is None:
        return None
    name, is_exact = resolved
    root = indexer.leaked_root

    impl_dirs: Counter[Path] = Counter()
    method_dir: Path | None = None
    # Use definition_index, not token_index: a class merely *called* from a file
    # (e.g. an external-SDK type like Attrib referenced from BrnBoostStrategy.cpp)
    # has no definition there and must not be filed under that caller's folder.
    for (cls, fn), locs in indexer.definition_index.items():
        if cls != name:
            continue
        for path, _off in locs:
            if path.suffix not in _IMPL_SUFFIXES:
                continue
            rel = path.parent.relative_to(root)
            impl_dirs[rel] += 1
            if fn_name and fn == fn_name and method_dir is None:
                method_dir = rel

    # 1. The exact method's implementation directory (strongest signal).
    if method_dir is not None:
        return method_dir
    # 2. The densest implementation directory for the class.
    if impl_dirs:
        top_dir, top_n = min(impl_dirs.items(), key=lambda kv: (-kv[1], kv[0].as_posix()))
        if is_exact or top_n / sum(impl_dirs.values()) >= _STRIP_DOMINANCE:
            return top_dir
        return None  # stripped name with scattered impls — not trustworthy
    # 3. Densest declaration directory (exact matches only; headers are noisy).
    if not is_exact:
        return None
    decl_dirs: Counter[Path] = Counter()
    for path, _off in indexer.class_index.get(name, []):
        decl_dirs[path.parent.relative_to(root)] += 1
    if decl_dirs:
        return min(decl_dirs.items(), key=lambda kv: (-kv[1], kv[0].as_posix()))[0]
    return None


def _class_filename(class_name: str) -> str:
    """Return a filesystem-safe ``.cpp`` filename for a (possibly namespaced) class.

    ``A::B::Name`` -> ``A_B_Name.cpp``. Windows forbids ``:`` in filenames, so the
    qualified class name must be flattened before it can be written.
    """
    return (class_name or "Unknown").replace("::", "_") + ".cpp"


def _extract_qualified_class(code: str, fn_name: str) -> str | None:
    """Best-effort recovery of the fully-qualified class from generated code.

    Looks for the method definition ``Qualified::fn_name(`` and, if present, an
    enclosing ``namespace A::B {`` to prepend.  Returns ``None`` for free
    functions that have no owning class, so the caller can leave the target as-is.
    """
    if not code or not fn_name:
        return None
    ident = r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*"
    m = re.search(rf"({ident})::{re.escape(fn_name)}\s*\(", code)
    cls = m.group(1) if m else None
    nm = re.search(rf"\bnamespace\s+({ident})\s*\{{", code)
    ns = nm.group(1) if nm else None
    if cls:
        if ns and not cls.startswith(ns):
            return f"{ns}::{cls}"
        return cls
    return None


def _looks_like_address(name: str | None) -> bool:
    """True when ``name`` is a raw-address placeholder rather than a real symbol.

    When a function is launched by address and the export has no symbol for it,
    the backend hands back the address string itself (``0x822c0e10``), an IDA
    ``sub_<hex>`` stub, or — from the Ghidra backend — an uppercase
    ``FUN_<hex>`` / ``LAB_<hex>`` / ``DAT_<hex>`` label. Those must not be
    written into source as the method name. Matched case-insensitively so the
    Ghidra (upper) and IDA (lower) spellings are both caught.
    """
    if not name:
        return True
    return bool(re.fullmatch(r"(?:sub_|loc_|fun_|lab_|dat_)?0?x?[0-9a-fA-F]+", name, re.IGNORECASE))


def _extract_method_def(code: str) -> tuple[str, str] | None:
    """Recover ``(qualified_class, method_name)`` from a generated method body.

    Unlike :func:`_extract_qualified_class`, this does **not** need the function
    name up front — it scans for the first ``Ret Qualified::method(...) {``
    *definition* (a body, not a declaration). Used when the function was launched
    by address and the backend gave no symbol, so the only trustworthy name is
    the one the LLM emitted in the code. Returns ``None`` for free functions or
    when no method definition is found.
    """
    if not code:
        return None
    ident = r"[A-Za-z_]\w*"
    qual = rf"{ident}(?:::{ident})*"
    # A definition: Qualified::name( ... ) [const] {  — the trailing brace
    # distinguishes it from a forward declaration ending in ';'.
    m = re.search(rf"\b({qual})::({ident})\s*\([^;{{}}]*\)\s*(?:const\s*)?\{{", code)
    if not m:
        return None
    cls, method = m.group(1), m.group(2)
    nm = re.search(rf"\bnamespace\s+({qual})\s*\{{", code)
    ns = nm.group(1) if nm else None
    if ns and not cls.startswith(ns):
        cls = f"{ns}::{cls}"
    return cls, method


def _heuristic_class_path(class_name: str) -> Path:
    """Last-resort path mapping for classes absent from the leaked source.

    Cgs* classes live under GameShared/GameClasses/<Module>/; everything else
    under GameSource/<ClassName>/. Filename is <ClassName>.cpp.
    """
    if class_name.startswith("Cgs"):
        subfolder_map = {
            "Module": ["CgsModule", "CgsDataBuffer", "CgsDataStructure",
                        "CgsModuleSingleBuffered"],
            "Core":   ["CgsAssert", "CgsStringUtils", "CgsCore"],
            "System": ["CgsHardwareInit", "CgsSystem"],
            "Containers": ["CgsFlagSet", "CgsContainers"],
        }
        sub = "Module"  # default
        for folder, prefixes in subfolder_map.items():
            if any(class_name.startswith(p) for p in prefixes):
                sub = folder
                break
        return _B5_SRC / "GameShared" / "GameClasses" / sub / _class_filename(class_name)
    return _B5_SRC / "GameSource" / class_name.replace("::", "_") / _class_filename(class_name)


def _class_to_b5_path(class_name: str, fn_name: str = "", leaked_root: str | None = None) -> Path:
    """Map a C++ class/method to its output path inside b5-decomp/src/.

    The folder layout is reconstructed from the leaked source tree (ground truth
    for the original engine directory structure); the filename is always
    <ClassName>.cpp so a class's recovered methods accumulate in one file. When
    the class is not found in the leaked source, fall back to a name heuristic.
    """
    indexer = _get_leaked_indexer(leaked_root)
    if indexer is not None:
        rel_dir = _leaked_relative_dir(indexer, class_name, fn_name)
        if rel_dir is not None:
            return _B5_SRC / rel_dir / _class_filename(class_name)
    return _heuristic_class_path(class_name)


def _cmake_rel(cpp_path: Path) -> str:
    """Return the CMakeLists-relative path string (forward slashes)."""
    return cpp_path.relative_to(_B5_SRC).as_posix()


def reset_b5_outputs() -> list[str]:
    """Delete every auto-registered output and empty the CMake AUTO block.

    Removes each file listed between the AUTO-BEGIN/AUTO-END markers in
    CMakeLists.txt (plus its parent directory when that leaves it empty) and
    rewrites the block with no entries. Only files inside ``b5-decomp/src``
    are touched. Returns the relative paths that were removed.
    """
    removed: list[str] = []
    if not _CMAKE.exists():
        return removed
    text = _CMAKE.read_text(encoding="utf-8")
    m = re.search(
        rf"^(?P<indent>[ \t]*){re.escape(_AUTO_BEGIN)}\n(?P<body>.*?)^[ \t]*{re.escape(_AUTO_END)}",
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if not m:
        return removed
    for line in m.group("body").splitlines():
        rel = line.strip()
        if not rel or rel.startswith("#"):
            continue
        target = (_B5_SRC / rel).resolve()
        # Never step outside b5-decomp/src.
        if _B5_SRC.resolve() not in target.parents:
            continue
        if target.exists():
            target.unlink()
            removed.append(rel)
            parent = target.parent
            if parent != _B5_SRC.resolve() and not any(parent.iterdir()):
                parent.rmdir()
    indent = m.group("indent")
    text = text[: m.start()] + f"{indent}{_AUTO_BEGIN}\n{indent}{_AUTO_END}" + text[m.end():]
    _CMAKE.write_text(text, encoding="utf-8")
    logger.info("b5-decomp: reset %d auto-registered outputs", len(removed))
    return removed


def _register_in_cmake(cpp_path: Path) -> None:
    """Ensure the .cpp file is listed inside the auto-managed block in CMakeLists.txt.

    If the auto-managed block does not exist yet it is inserted before the
    closing ``)`` of the first ``target_sources(... PRIVATE`` section.
    """
    if not _CMAKE.exists():
        return
    rel = _cmake_rel(cpp_path)
    text = _CMAKE.read_text(encoding="utf-8")
    if rel in text:
        return  # already registered

    if _AUTO_BEGIN in text and _AUTO_END in text:
        # Insert before the end-marker *line*, preserving its indentation.
        # A bare ``text.replace(_AUTO_END, ...)`` would absorb the marker's
        # leading whitespace into the inserted line (mis-indenting both).
        text = re.sub(
            rf"^([ \t]*){re.escape(_AUTO_END)}",
            rf"        {rel}\n\g<1>{_AUTO_END}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
    else:
        # Create the auto-managed block just before the ``    PUBLIC`` section.
        insert_block = (
            f"    {_AUTO_BEGIN}\n"
            f"        {rel}\n"
            f"    {_AUTO_END}\n"
        )
        text = re.sub(
            r"(\n    PUBLIC)",
            f"\n{insert_block}\n    PUBLIC",
            text,
            count=1,
        )
    _CMAKE.write_text(text, encoding="utf-8")
    logger.info("CMakeLists.txt: registered %s", rel)


def _write_to_b5decomp(result: ReversalResult, leaked_root: str | None = None) -> None:
    """Append the recovered function code into the per-class .cpp in b5-decomp/src/.

    Only called when the result is considered good enough to commit
    (checker PASS or parity GREEN/YELLOW or result.success). ``leaked_root`` lets
    the folder layout be reconstructed from the leaked source tree.
    """
    class_name = result.target.class_name or "Unknown"
    fn_name    = result.target.function_name or "unknown"
    code       = result.code

    out_path = _class_to_b5_path(class_name, fn_name, leaked_root)
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Build a section header so the file stays readable
        header = (
            f"// --- {class_name}::{fn_name} "
            f"[{result.target.address}] ---\n"
        )
        separator = "\n" + "/" * 80 + "\n"

        if out_path.exists():
            existing = out_path.read_text(encoding="utf-8")
            # Skip if this function is already present (idempotent re-runs)
            if f"{class_name}::{fn_name}" in existing:
                return
            out_path.write_text(existing + separator + header + code + "\n", encoding="utf-8")
        else:
            out_path.write_text(header + code + "\n", encoding="utf-8")

        logger.info("b5-decomp: wrote %s::%s -> %s", class_name, fn_name, out_path)
        emit_event("b5decomp.written", {"target": result.target, "path": str(out_path)})

        _register_in_cmake(out_path)
    except OSError as exc:
        logger.warning("b5-decomp write failed for %s::%s: %s", class_name, fn_name, exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def reverse_single(
    target: FunctionTarget,
    config: ReAgentConfig,
    backend: REBackend,
    llm: LLMProvider,
    session: Session | None = None,
    output_dir: Path | None = None,
    indexer: SourceIndexer | None = None,
    checker_llm: LLMProvider | None = None,
) -> ReversalResult:
    """Reverse a single function: agent loop -> optional parity check -> record.

    Args:
        output_dir: If provided, write the generated code to a file in this
            directory.  The file is named ``<address>_<class>_<func>.cpp``.
        indexer: Pre-built source indexer.  When running multiple functions
            in the same class, callers should build the indexer once and pass
            it here to avoid re-scanning the entire source tree each time.
        checker_llm: LLM provider for the checker agent.  When ``None`` the
            checker reuses ``llm`` (the reverser provider).
    """
    emit_event("reverse_single.started", {"target": target})
    log_dir = Path(config.output.log_dir) if config.output.log_dir else None

    result = run_fix_loop(
        target=target,
        backend=backend,
        reverser_llm=llm,
        checker_llm=checker_llm or llm,
        max_rounds=config.orchestrator.max_review_rounds,
        log_dir=log_dir,
        source_root=Path(config.project_profile.source_root),
        project_profile=config.project_profile,
        indexer=indexer,
        session=session,
        report_dir=Path(config.output.report_dir),
        objective_verifier_enabled=config.orchestrator.objective_verifier_enabled,
        objective_call_count_tolerance=config.orchestrator.objective_call_count_tolerance,
        objective_control_flow_tolerance=config.orchestrator.objective_control_flow_tolerance,
        ida_bin=config.backend.ida_bin,
    )

    # Recover the fully-qualified class — and, when launched by address with no
    # symbol, the real method name too — from the code the LLM emitted. The X360
    # export often lacks Class::; for fully stripped functions the backend hands
    # back the address itself as the "name" (e.g. 0x822c0e10), which would
    # otherwise be written as Unknown::0x822c0e10 into GameSource/Unknown/. Prefer
    # what the LLM emitted this run; otherwise reuse a class an earlier run — or an
    # explicit --class — already recorded for this address. Routes the file to the
    # right class folder and lets parity locate the body on re-runs.
    if result.code:
        fn_is_placeholder = _looks_like_address(target.function_name)
        new_class = target.class_name
        new_fn = target.function_name
        if not new_class or fn_is_placeholder:
            method_def = _extract_method_def(result.code)
            if method_def:
                emitted_class, emitted_fn = method_def
                if not new_class:
                    new_class = emitted_class
                if fn_is_placeholder and emitted_fn:
                    new_fn = emitted_fn
        if not new_class:
            # Fall back to a class the LLM named against the known fn name, then
            # to one a prior run recorded for this address.
            new_class = _extract_qualified_class(result.code, target.function_name)
            if not new_class and session is not None:
                new_class = session.get_known_class(target.address)
        if new_class != target.class_name or new_fn != target.function_name:
            target = replace(target, class_name=new_class or target.class_name,
                             function_name=new_fn or target.function_name)
            result = replace(result, target=target)
            emit_event("class.recovered",
                       {"target": target, "class_name": target.class_name,
                        "function_name": target.function_name})

    # Write generated code to a file so users don't have to dig through logs
    if result.code:
        code_dir = output_dir or (Path(config.output.report_dir) / "code")
        try:
            code_dir.mkdir(parents=True, exist_ok=True)
            safe_name = f"{target.address}_{target.class_name}_{target.function_name}.cpp"
            safe_name = safe_name.replace("::", "_").replace("/", "_")
            code_path = code_dir / safe_name
            code_path.write_text(result.code, encoding="utf-8")
            logger.info("Code written to %s", code_path)
            emit_event(
                "code.written",
                {
                    "target": target,
                    "path": str(code_path),
                    "code_length": len(result.code),
                },
            )
        except OSError as exc:
            logger.warning("Failed to write code file: %s", exc)
            emit_event(
                "code.write_failed",
                {
                    "target": target,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

    # Run parity check if enabled and code was produced
    if config.parity.enabled and result.code:
        try:
            emit_event("parity.started", {"target": target})
            if indexer is None:
                source_root = Path(config.project_profile.source_root)
                indexer = SourceIndexer(source_root, config.project_profile)
            # Score the freshly generated candidate directly (it is not on disk
            # until _write_to_b5decomp below); fall back to the indexed source
            # tree for re-runs where the body already exists.
            source = (
                indexer.match_code(result.code, target.class_name, target.function_name)
                or indexer.find(target.class_name, target.function_name)
            )

            # Fetch Ghidra data from the backend for signal checks
            ghidra_data = None
            if backend.capabilities.has_decompile:
                try:
                    ghidra_data = fetch_ghidra_data(target.address, backend)
                except Exception:
                    logger.debug("Ghidra data fetch failed for %s, running source-only", target.address, exc_info=True)

            status, findings = score_single(
                entry=_target_to_hook(target),
                source=source,
                ghidra=ghidra_data,
                config=config.parity,
            )
            emit_event(
                "parity.completed",
                {
                    "target": target,
                    "status": status.value,
                    "findings": findings,
                },
            )
            result = ReversalResult(
                target=result.target,
                code=result.code,
                checker_verdict=result.checker_verdict,
                objective_verdict=result.objective_verdict,
                parity_status=status,
                parity_findings=findings,
                rounds_used=result.rounds_used,
                success=result.success,
            )
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Parity check failed for %s: %s", target.address, exc)
            emit_event(
                "parity.failed",
                {
                    "target": target,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )

    if session:
        session.record_result(result)
        emit_event("session.recorded", {"target": target, "result": result})

    # --- Write to b5-decomp automatically on success ---
    # Criteria: checker passed OR parity is GREEN or YELLOW OR result.success.
    _checker_ok = (
        result.checker_verdict is not None
        and result.checker_verdict.verdict == Verdict.PASS
    )
    _parity_ok = result.parity_status in (ParityStatus.GREEN, ParityStatus.YELLOW)
    if result.code and (_checker_ok or _parity_ok or result.success):
        _write_to_b5decomp(result, config.project_profile.leaked_source_root)

    emit_event("reverse_single.completed", {"target": target, "result": result})
    return result


def _target_to_hook(target: FunctionTarget) -> HookEntry:
    return HookEntry(
        class_path=target.class_name,
        fn_name=target.function_name,
        address=target.address,
        reversed=True,
        locked=False,
        is_virtual=False,
    )
