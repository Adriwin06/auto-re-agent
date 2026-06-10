"""Tests for the IDAFallbackManager backend wrapper."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from re_agent.backend.ida_fallback import IDAFallbackManager


def test_get_idb_path_with_no_config(tmp_path: Path) -> None:
    manager = IDAFallbackManager()
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        idb = manager.get_idb_path(tmp_path)
        # Default should fall back to X360
        assert idb == tmp_path / "IDA Files" / "BURNOUT_X360_ARTIST.XEX.i64"


def _write_config(tmp_path: Path, program_name: str) -> None:
    (tmp_path / "ghidra-bridge.yaml").write_text(
        f"ghidra:\n  program_name: {program_name}\n", encoding="utf-8"
    )


def _make_db(tmp_path: Path, filename: str) -> Path:
    ida_dir = tmp_path / "IDA Files"
    ida_dir.mkdir(exist_ok=True)
    db = ida_dir / filename
    db.touch()
    return db


def test_get_idb_path_with_x360_config(tmp_path: Path) -> None:
    manager = IDAFallbackManager()
    _write_config(tmp_path, "BURNOUT_X360_ARTIST.XEX")
    db = _make_db(tmp_path, "BURNOUT_X360_ARTIST.XEX.i64")
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        assert manager.get_idb_path(tmp_path) == db


def test_get_idb_path_with_ps3_config(tmp_path: Path) -> None:
    manager = IDAFallbackManager()
    _write_config(tmp_path, "Burnout_External_PS3.ELF")
    db = _make_db(tmp_path, "Burnout_External_PS3.ELF.i64")
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        assert manager.get_idb_path(tmp_path) == db


def test_get_idb_path_generalizes_to_pc_builds(tmp_path: Path) -> None:
    # program_name -> '<program_name>.i64' for any target (here: BPR).
    manager = IDAFallbackManager()
    _write_config(tmp_path, "BurnoutPR.exe")
    db = _make_db(tmp_path, "BurnoutPR.exe.i64")
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        assert manager.get_idb_path(tmp_path) == db


def test_get_idb_path_missing_db_falls_back_to_x360(tmp_path: Path) -> None:
    # Config names a build whose .i64 isn't present (e.g. DecFIGS not yet exported).
    manager = IDAFallbackManager()
    _write_config(tmp_path, "DecFIGS_Burnout_Internal_PS3.ELF")
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        idb = manager.get_idb_path(tmp_path)
        assert idb == tmp_path / "IDA Files" / "BURNOUT_X360_ARTIST.XEX.i64"


def test_decompile_fallback_execution(tmp_path: Path) -> None:
    # Set up mock files and directories
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    script_file = tools_dir / "ida_decompile.py"
    script_file.touch()

    ida_dir = tmp_path / "IDA Files"
    ida_dir.mkdir()
    db_file = ida_dir / "BURNOUT_X360_ARTIST.XEX.i64"
    db_file.touch()

    ida_bin = tmp_path / "idat64.exe"
    ida_bin.touch()

    manager = IDAFallbackManager(ida_bin=str(ida_bin))

    mock_pseudocode = "void CPhysics::Update() { /* mock */ }"

    # We mock run_cmd_split to simulate IDA executing successfully.
    # When it executes, we expect it to write mock_pseudocode to the temp out file.
    def fake_run(args: list[str], timeout_s: int) -> tuple[int, str, str]:
        # Assert arguments are correct
        assert args[0] == str(ida_bin)
        assert args[1] == "-A"
        assert args[2].startswith(f"-S{script_file}")
        assert args[3] == str(db_file)

        # Assert environment variables were set
        assert os.environ.get("IDA_DECOMPILE_ADDR") == "0x6F86A0"
        out_path = os.environ.get("IDA_DECOMPILE_OUT")
        assert out_path is not None

        # Write output file
        Path(out_path).write_text(mock_pseudocode, encoding="utf-8")
        return 0, "success", ""

    with patch("pathlib.Path.cwd", return_value=tmp_path), \
         patch("re_agent.backend.ida_fallback.run_cmd_split", side_effect=fake_run), \
         patch("os.path.exists", return_value=True):
        code = manager.decompile("0x6F86A0")
        assert code == mock_pseudocode
