"""Tests for class recovery + safe filename helpers in single.py."""
from __future__ import annotations

from re_agent.orchestrator.single import _class_filename, _extract_qualified_class


def test_extract_qualified_class_with_namespace() -> None:
    code = (
        "namespace CgsSound::Playback {\n"
        "std::uint64_t Name::MakeHash(const char* s) { return 1; }\n"
        "}\n"
    )
    assert _extract_qualified_class(code, "MakeHash") == "CgsSound::Playback::Name"


def test_extract_qualified_class_method_only() -> None:
    code = "int CPhysics::Update(float dt) { return 0; }"
    assert _extract_qualified_class(code, "Update") == "CPhysics"


def test_extract_qualified_class_free_function_is_none() -> None:
    assert _extract_qualified_class("int MakeHash() { return 0; }", "MakeHash") is None
    assert _extract_qualified_class("", "MakeHash") is None


def test_class_filename_flattens_namespaces() -> None:
    assert _class_filename("CgsSound::Playback::Name") == "CgsSound_Playback_Name.cpp"
    assert _class_filename("CPhysics") == "CPhysics.cpp"
    assert _class_filename("") == "Unknown.cpp"
    # Result must contain no characters illegal in Windows filenames.
    assert ":" not in _class_filename("A::B::C")
