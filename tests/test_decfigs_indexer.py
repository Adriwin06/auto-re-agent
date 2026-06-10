"""Tests for the DecFIGS source-structure indexer."""
from __future__ import annotations

import json
from pathlib import Path

from re_agent.parity.decfigs_indexer import DecfigsSourceIndexer, demangle_tokens


def test_demangle_nested():
    assert demangle_tokens("._ZN11BrnDirector14MomentSelector8DestructEv") == [
        "BrnDirector", "MomentSelector", "Destruct",
    ]
    assert demangle_tokens("._ZN6Attrib8TypeDesc6LookupEy") == [
        "Attrib", "TypeDesc", "Lookup",
    ]


def test_demangle_garbage_returns_empty():
    assert demangle_tokens("not_mangled") == []
    assert demangle_tokens("") == []


def _write(root: Path, funcs: list[dict]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    data = {f"{i:04X}": rec for i, rec in enumerate(funcs)}
    (root / "decfigs_func_files.json").write_text(json.dumps(data), encoding="utf-8")


def test_missing_export_noop(tmp_path: Path):
    ix = DecfigsSourceIndexer(tmp_path / "nope")
    assert ix.available is False
    assert ix.lookup("A", "B") == ""


def test_definition_file_picked_by_name(tmp_path: Path):
    # Prologue is inlined container code, so home_file is a header; the real
    # definition file is named after the class and listed under inlined_files.
    _write(tmp_path, [{
        "name": "._ZN11BrnDirector14MomentSelector8DestructEv",
        "home_file": "GameShared/GameClasses/Containers/CgsArray.h",
        "inlined_files": ["GameSource/Director/MomentController/BrnMomentSelector.cpp"],
    }])
    ix = DecfigsSourceIndexer(tmp_path)
    out = ix.lookup("MomentSelector", "Destruct")
    assert "BrnMomentSelector.cpp" in out
    assert "Home file" in out
    # CgsArray.h now correctly demoted to an inlined dependency
    assert "CgsArray.h" in out and "Inlines code from 1" in out


def test_class_filter_disambiguates(tmp_path: Path):
    _write(tmp_path, [
        {"name": "._ZN3Foo6UpdateEv", "home_file": "Foo.cpp", "inlined_files": []},
        {"name": "._ZN3Bar6UpdateEv", "home_file": "Bar.cpp", "inlined_files": []},
    ])
    ix = DecfigsSourceIndexer(tmp_path)
    assert "Foo.cpp" in ix.lookup("Foo", "Update")
    assert "Bar.cpp" in ix.lookup("Bar", "Update")


def test_ambiguous_method_without_class_returns_empty(tmp_path: Path):
    _write(tmp_path, [
        {"name": "._ZN3Foo6UpdateEv", "home_file": "Foo.cpp", "inlined_files": []},
        {"name": "._ZN3Bar6UpdateEv", "home_file": "Bar.cpp", "inlined_files": []},
    ])
    ix = DecfigsSourceIndexer(tmp_path)
    assert ix.lookup("", "Update") == ""  # two candidates, no class to pick


def test_no_inlining_message(tmp_path: Path):
    _write(tmp_path, [{
        "name": "._ZN3Foo3BarEv", "home_file": "Foo.cpp", "inlined_files": [],
    }])
    ix = DecfigsSourceIndexer(tmp_path)
    out = ix.lookup("Foo", "Bar")
    assert "No inlining detected" in out
