"""Tests for the IDAFallbackManager backend wrapper."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from re_agent.backend.ida_fallback import IDAFallbackManager


def test_get_idb_path_with_no_config(tmp_path: Path) -> None:
    manager = IDAFallbackManager()
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        idb = manager.get_idb_path(tmp_path)
        # Default should fall back to X360
        assert idb == tmp_path / "IDA Files" / "BURNOUT_X360_ARTIST.XEX.i64"


def test_get_idb_path_with_x360_config(tmp_path: Path) -> None:
    manager = IDAFallbackManager()
    config_yaml = tmp_path / "ghidra-bridge.yaml"
    config_yaml.write_text(
        """
        ghidra:
          program_name: BURNOUT_X360_ARTIST.XEX
        """,
        encoding="utf-8",
    )
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        idb = manager.get_idb_path(tmp_path)
        assert idb == tmp_path / "IDA Files" / "BURNOUT_X360_ARTIST.XEX.i64"


def test_get_idb_path_with_ps3_config(tmp_path: Path) -> None:
    manager = IDAFallbackManager()
    config_yaml = tmp_path / "ghidra-bridge.yaml"
    config_yaml.write_text(
        """
        ghidra:
          program_name: Burnout_External_PS3.ELF
        """,
        encoding="utf-8",
    )
    with patch("pathlib.Path.cwd", return_value=tmp_path):
        idb = manager.get_idb_path(tmp_path)
        assert idb == tmp_path / "IDA Files" / "Burnout_External_PS3.ELF.i64"


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
