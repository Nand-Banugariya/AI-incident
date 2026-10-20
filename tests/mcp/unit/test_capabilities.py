"""describe_capabilities payload: only the agreed vocabulary."""

import pytest

from mcp_common.capabilities import describe


def test_describe_sorts_and_returns_payload():
    assert describe("local-code", ["source_code"], "0.1.0") == {
        "server": "local-code",
        "capabilities": ["source_code"],
        "version": "0.1.0",
    }


def test_unknown_capability_rejected():
    with pytest.raises(ValueError, match="metrics"):
        describe("x", ["source_code", "metrics"], "0.1.0")
