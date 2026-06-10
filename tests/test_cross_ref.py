"""Tests for the cross-build name-keyed decompile lookup."""
from __future__ import annotations

import json
from pathlib import Path

from re_agent.backend.cross_ref import CrossRefManager


def _build(root: Path, funcs: dict[str, tuple[str, str]]) -> Path:
    """funcs: address -> (symbol_name, decompiled_body). Writes index + bodies."""
    root.mkdir(parents=True, exist_ok=True)
    index = {addr: {"name": name, "num_callers": 0} for addr, (name, _) in funcs.items()}
    (root / "_index.json").write_text(json.dumps(index), encoding="utf-8")
    for addr, (name, body) in funcs.items():
        (root / f"{addr}.json").write_text(
            json.dumps({"address": addr, "name": name, "decompiled": body}),
            encoding="utf-8",
        )
    return root


def test_matches_mangled_and_demangled(tmp_path: Path) -> None:
    # Same function: demangled in one build, MSVC-mangled in another.
    b1 = _build(tmp_path / "x360", {
        "0001": ("BrnDepthOfField::SetParams", "void setp_x360(void){}"),
    })
    b2 = _build(tmp_path / "bpr", {
        "00aa": ("?SetParams@BrnDepthOfField@@QEAAXXZ", "void setp_bpr(void){}"),
        "00bb": ("?Unrelated@Other@@QEAAXXZ", "void UNRELATED_BODY(void){}"),
    })
    mgr = CrossRefManager({"X360": b1, "BPR": b2})
    out = mgr.lookup("BrnDepthOfField", "SetParams")
    assert "setp_x360" in out and "setp_bpr" in out
    assert "UNRELATED_BODY" not in out  # class+func token filter excludes it


def test_class_token_filters_common_method_names(tmp_path: Path) -> None:
    b = _build(tmp_path / "ps3", {
        "01": ("FooBar::Update", "void foo_update(void){}"),
        "02": ("BazQux::Update", "void baz_update(void){}"),
    })
    mgr = CrossRefManager({"PS3": b})
    out = mgr.lookup("FooBar", "Update")
    assert "foo_update" in out
    assert "baz_update" not in out  # filtered by the FooBar class token


def test_no_match_returns_empty(tmp_path: Path) -> None:
    b = _build(tmp_path / "tub", {"01": ("Something::Else", "x")})
    mgr = CrossRefManager({"TUB": b})
    assert mgr.lookup("Nope", "Missing") == ""


def test_missing_export_dir_is_skipped(tmp_path: Path) -> None:
    mgr = CrossRefManager({"Gone": tmp_path / "does_not_exist"})
    assert mgr.lookup("A", "B") == ""


def test_body_truncation(tmp_path: Path) -> None:
    big = "x" * 5000
    b = _build(tmp_path / "x360", {"01": ("N::Big", big)})
    mgr = CrossRefManager({"X360": b}, max_chars_per_body=100)
    out = mgr.lookup("N", "Big")
    assert "[truncated]" in out and len(out) < 1000


def test_invalid_function_name_rejected(tmp_path: Path) -> None:
    b = _build(tmp_path / "x360", {"01": ("N::Op", "x")})
    mgr = CrossRefManager({"X360": b})
    # operator-ish / non-identifier names should not run a substring sweep
    assert mgr.lookup("N", "operator+") == ""
