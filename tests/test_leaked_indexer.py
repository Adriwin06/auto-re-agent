"""Tests for the LeakedSourceIndexer."""
from __future__ import annotations

from pathlib import Path
import pytest

from re_agent.parity.leaked_indexer import LeakedSourceIndexer


def test_leaked_indexer_finds_class_and_function(tmp_path: Path) -> None:
    # Create mock split files
    class_h = tmp_path / "CPhysics.h"
    class_h.write_text(
        """
        #ifndef CPHYSICS_H
        #define CPHYSICS_H

        class CPhysics : public ISubsystem {
        public:
            int m_val;
            void Update(float dt);
            void Reset();
        };

        struct OtherStruct {
            int x;
        };

        #endif
        """,
        encoding="utf-8",
    )

    class_cpp = tmp_path / "CPhysics.cpp"
    class_cpp.write_text(
        """
        #include "CPhysics.h"

        void CPhysics::Update(float dt) {
            m_val += 1;
            if (m_val > 10) {
                m_val = 0;
            }
        }

        void CPhysics::Reset() : m_val(0) {
            // constructor-like initializer list
        }
        """,
        encoding="utf-8",
    )

    indexer = LeakedSourceIndexer(tmp_path)

    # 1. Test function definition retrieval
    func_body = indexer.find_function("CPhysics", "Update")
    assert func_body is not None
    assert "void CPhysics::Update(float dt)" in func_body
    assert "m_val += 1;" in func_body

    # 2. Test initializer list function retrieval
    reset_body = indexer.find_function("CPhysics", "Reset")
    assert reset_body is not None
    assert "void CPhysics::Reset()" in reset_body

    # 3. Test class definition retrieval
    class_def = indexer.find_class_definition("CPhysics")
    assert class_def is not None
    assert "class CPhysics : public ISubsystem {" in class_def
    assert "void Update(float dt);" in class_def

    # 4. Test struct definition retrieval
    struct_def = indexer.find_class_definition("OtherStruct")
    assert struct_def is not None
    assert "struct OtherStruct {" in struct_def

    # 5. Non-existent lookups return None
    assert indexer.find_function("CPhysics", "NonExistent") is None
    assert indexer.find_class_definition("NonExistentClass") is None
