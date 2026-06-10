"""count_calls must not misclassify calls when there is no stub-call prefix."""
from __future__ import annotations

from re_agent.utils.text import count_calls


def test_empty_prefix_counts_no_plugin_calls() -> None:
    body = "Store(h, s); BeginAssert(); FireAssert(a, b, c); EndAssert();"
    total, plugin, non_plugin = count_calls(body, stub_call_prefix="")
    assert plugin == 0
    assert non_plugin == total == 4


def test_real_prefix_still_classifies() -> None:
    body = "plugin::CallAndReturn(x); RealCall(y);"
    total, plugin, non_plugin = count_calls(body, stub_call_prefix="plugin::Call")
    assert total == 2
    assert plugin == 1
    assert non_plugin == 1
