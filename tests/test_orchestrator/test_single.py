"""Tests for single function orchestrator."""
from __future__ import annotations

from re_agent.config.schema import ReAgentConfig
from re_agent.core.models import FunctionTarget


def test_dry_run_smoke() -> None:
    """Smoke test that config + target creation works."""
    config = ReAgentConfig.create_default()
    target = FunctionTarget(
        address="0x6F86A0",
        class_name="CTrain",
        function_name="ProcessControl",
    )
    assert target.address == "0x6F86A0"
    assert config.orchestrator.max_review_rounds == 4


def test_merge_cpp_sources() -> None:
    from re_agent.orchestrator.single import _merge_cpp_sources

    # Test case 1: Appending a function inside the same namespace
    existing = """#include <cstddef>

namespace Attrib {

void SetAttribute() {
}

} // namespace Attrib
"""

    new_code = """// --- Attrib::StringToKey ---
#include <cstdint>
#include <cstddef>

namespace Attrib {

void StringToKey() {
}

} // namespace Attrib
"""

    expected = """#include <cstddef>
#include <cstdint>

namespace Attrib {

void SetAttribute() {
}

////////////////////////////////////////////////////////////////////////////////
// --- Attrib::StringToKey ---
void StringToKey() {
}
} // namespace Attrib
"""

    merged = _merge_cpp_sources(existing, new_code)
    assert merged.strip() == expected.strip()

