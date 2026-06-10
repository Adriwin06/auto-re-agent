"""Tests for data-driven b5-decomp output path mapping (single.py)."""
from __future__ import annotations

from pathlib import Path

from re_agent.orchestrator import single as s
from re_agent.parity.leaked_indexer import LeakedSourceIndexer


def _make_leaked_tree(root: Path) -> None:
    """A miniature leaked source tree exercising each resolution path."""
    boost = root / "GameSource" / "World" / "Boost"
    boost.mkdir(parents=True)
    (boost / "BoostManager.cpp").write_text(
        "void BoostManager::Update() {}\nvoid BoostManager::Reset() {}\n",
        encoding="utf-8",
    )

    physics = root / "GameShared" / "Physics"
    physics.mkdir(parents=True)
    (physics / "Physics.h").write_text(
        "class CgsPhysics { void Step(); };\n", encoding="utf-8"
    )
    (physics / "Physics.cpp").write_text(
        "void CgsPhysics::Step() {}\n", encoding="utf-8"
    )

    # A generic name scattered across many directories — must NOT route on a
    # prefix-stripped lookup.
    for i in range(5):
        d = root / "GameSource" / f"Mod{i}"
        d.mkdir(parents=True)
        (d / f"Mod{i}.cpp").write_text(
            f"void Module::Func{i}() {{}}\n", encoding="utf-8"
        )


def _resolve(root: Path, cls: str, fn: str = "") -> Path:
    """Run the resolver against a synthetic tree, bypassing the lru_cache."""
    indexer = LeakedSourceIndexer(root)
    rel = s._leaked_relative_dir(indexer, cls, fn)
    return rel


def test_exact_class_routes_to_impl_dir(tmp_path: Path) -> None:
    _make_leaked_tree(tmp_path)
    assert _resolve(tmp_path, "BoostManager") == Path("GameSource/World/Boost")


def test_prefix_stripped_match(tmp_path: Path) -> None:
    # BrnBoostManager -> BoostManager (Brn prefix stripped).
    _make_leaked_tree(tmp_path)
    assert _resolve(tmp_path, "BrnBoostManager") == Path("GameSource/World/Boost")


def test_method_specific_routing_wins(tmp_path: Path) -> None:
    _make_leaked_tree(tmp_path)
    assert _resolve(tmp_path, "CgsPhysics", "Step") == Path("GameShared/Physics")


def test_generic_stripped_name_rejected(tmp_path: Path) -> None:
    # BrnModule -> "Module" is scattered across 5 dirs; must reject (None) so the
    # caller falls back to the heuristic rather than guessing a wrong directory.
    _make_leaked_tree(tmp_path)
    assert _resolve(tmp_path, "BrnModule") is None


def test_unknown_class_falls_back_to_heuristic(tmp_path: Path) -> None:
    _make_leaked_tree(tmp_path)
    assert _resolve(tmp_path, "TotallyUnknown") is None


def test_class_to_b5_path_uses_heuristic_when_no_leaked_root() -> None:
    # No leaked_root -> pure heuristic mapping (unchanged legacy behaviour).
    p = s._class_to_b5_path("CgsCore", "", None)
    assert p == s._B5_SRC / "GameShared" / "GameClasses" / "Core" / "CgsCore.cpp"
    p2 = s._class_to_b5_path("BrnThing", "", None)
    assert p2 == s._B5_SRC / "GameSource" / "BrnThing" / "BrnThing.cpp"
