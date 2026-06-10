"""Tests for the Renderware `rw::` type indexer."""
from __future__ import annotations

import json
from pathlib import Path

from re_agent.parity.rwcore_indexer import RwcoreTypeIndexer


def _write_export(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    structs = {
        "rw::LLLink": {
            "name": "rw::LLLink", "size": 24, "fields": [
                {"offset": 0, "type": "pointer", "name": "next", "size": 8},
                {"offset": 8, "type": "pointer", "name": "prev", "size": 8},
                {"offset": 16, "type": "pointer", "name": "data", "size": 8},
            ],
        },
        "rw::RGBA": {
            "name": "rw::RGBA", "size": 4, "fields": [
                {"offset": 0, "type": "dword", "name": "m_rgba", "size": 4},
            ],
        },
        # non-rw type must be ignored entirely
        "eastl::allocator": {"name": "eastl::allocator", "size": 8, "fields": []},
    }
    enums = {
        "rw::core::debug::host::HostMode": {
            "name": "rw::core::debug::host::HostMode", "size": 4,
            "values": [{"name": "HOST_READONLY", "value": 1},
                       {"name": "HOST_BINARY", "value": 64}],
        },
    }
    (root / "_structs.json").write_text(json.dumps(structs), encoding="utf-8")
    (root / "_enums.json").write_text(json.dumps(enums), encoding="utf-8")


def test_missing_export_is_noop(tmp_path: Path) -> None:
    ix = RwcoreTypeIndexer(tmp_path / "nope")
    assert ix.available is False
    assert ix.lookup("rw::LLLink") == ""


def test_loads_only_rw_namespace(tmp_path: Path) -> None:
    _write_export(tmp_path)
    ix = RwcoreTypeIndexer(tmp_path)
    assert ix.available is True
    assert "rw::LLLink" in ix.structs
    assert "eastl::allocator" not in ix.structs  # foreign types filtered out


def test_qualified_name_match(tmp_path: Path) -> None:
    _write_export(tmp_path)
    ix = RwcoreTypeIndexer(tmp_path)
    out = ix.lookup("auto* l = (rw::LLLink*)p;")
    assert "struct rw::LLLink (size: 24)" in out
    assert "next" in out and "+0x10 pointer data" in out


def test_distinctive_short_name_match(tmp_path: Path) -> None:
    _write_export(tmp_path)
    ix = RwcoreTypeIndexer(tmp_path)
    out = ix.lookup("HostMode mode; RGBA color;")
    assert "HostMode" in out and "HOST_BINARY = 64" in out
    assert "rw::RGBA" in out


def test_ambiguous_or_unknown_short_name_suppressed(tmp_path: Path) -> None:
    _write_export(tmp_path)
    ix = RwcoreTypeIndexer(tmp_path)
    # "Device"/"Channel" are in the ambiguous set; unknown words match nothing.
    assert ix.lookup("Device d; somethingElse x;") == ""


def test_max_types_cap(tmp_path: Path) -> None:
    _write_export(tmp_path)
    ix = RwcoreTypeIndexer(tmp_path)
    out = ix.lookup("rw::LLLink rw::RGBA HostMode", max_types=1)
    # only the first (qualified) hit rendered
    assert out.count("struct ") + out.count("enum ") == 1
