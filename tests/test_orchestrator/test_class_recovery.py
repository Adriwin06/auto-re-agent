"""Tests for class recovery + safe filename helpers in single.py."""
from __future__ import annotations

from re_agent.orchestrator.single import (
    _class_filename,
    _extract_method_def,
    _extract_qualified_class,
    _looks_like_address,
)


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


def test_looks_like_address_detects_placeholders() -> None:
    assert _looks_like_address("0x822c0e10")
    assert _looks_like_address("sub_822c0e10")
    assert _looks_like_address("822c0e10")
    assert _looks_like_address("")
    assert _looks_like_address(None)
    # Real symbols must not be mistaken for addresses.
    assert not _looks_like_address("AddBoost")
    assert not _looks_like_address("Update")


def test_extract_method_def_recovers_class_and_name() -> None:
    # The reported bug: launched by address, so no fn name is known up front.
    code = (
        "// --- comment line ---\n"
        "void CBoostMeter::AddBoost(double amount)\n"
        "{\n"
        "    if (m_boostActive == 0) return;\n"
        "}\n"
    )
    assert _extract_method_def(code) == ("CBoostMeter", "AddBoost")


def test_extract_method_def_with_namespace() -> None:
    code = (
        "namespace CgsSound::Playback {\n"
        "std::uint64_t Name::MakeHash(const char* s) { return 1; }\n"
        "}\n"
    )
    assert _extract_method_def(code) == ("CgsSound::Playback::Name", "MakeHash")


def test_extract_method_def_const_method() -> None:
    code = "bool CFoo::IsReady() const { return true; }"
    assert _extract_method_def(code) == ("CFoo", "IsReady")


def test_extract_method_def_free_function_is_none() -> None:
    assert _extract_method_def("int MakeHash() { return 0; }") is None
    assert _extract_method_def("") is None
    # A forward declaration (no body) is not a definition.
    assert _extract_method_def("void CFoo::Bar(int x);") is None


def test_class_filename_flattens_namespaces() -> None:
    assert _class_filename("CgsSound::Playback::Name") == "CgsSound_Playback_Name.cpp"
    assert _class_filename("CPhysics") == "CPhysics.cpp"
    assert _class_filename("") == "Unknown.cpp"
    # Result must contain no characters illegal in Windows filenames.
    assert ":" not in _class_filename("A::B::C")
