"""Session.get_known_class remembers a discovered class per address."""
from __future__ import annotations

from pathlib import Path

from re_agent.core.models import FunctionTarget, ParityStatus, ReversalResult
from re_agent.core.session import Session


def _result(addr: str, cls: str, fn: str) -> ReversalResult:
    return ReversalResult(
        target=FunctionTarget(address=addr, class_name=cls, function_name=fn),
        code="x",
        checker_verdict=None,
        objective_verdict=None,
        parity_status=ParityStatus.YELLOW,
        parity_findings=[],
        rounds_used=1,
        success=True,
    )


def test_get_known_class_roundtrip(tmp_path: Path) -> None:
    s = Session(tmp_path / "sess.json")
    s.record_result(_result("0x82689a50", "CgsSound::Playback::Name", "MakeHash"))
    assert s.get_known_class("0x82689a50") == "CgsSound::Playback::Name"
    # Normalization: lookup works regardless of 0x / case.
    assert s.get_known_class("82689a50") == "CgsSound::Playback::Name"


def test_get_known_class_ignores_unknown_and_missing(tmp_path: Path) -> None:
    s = Session(tmp_path / "sess.json")
    s.record_result(_result("0x1000", "Unknown", "foo"))
    assert s.get_known_class("0x1000") is None
    assert s.get_known_class("0xdeadbeef") is None
