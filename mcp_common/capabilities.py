"""Capability vocabulary and the `describe_capabilities` payload every server exposes.

MCP standardises tools, not capabilities; this is our convention. The registry copies a server's answer into
`data_source_capabilities`, and the health task uses the same tool as its cheap liveness probe.
"""

CAPABILITIES = frozenset({
    "application_logs",
    "database_logs",
    "database_state",
    "database_schema",
    "source_code",
    "pod_state",
    "pod_events",
    "deployment_info",
})


def describe(server: str, capabilities: list[str], version: str) -> dict:
    unknown = sorted(set(capabilities) - CAPABILITIES)
    if unknown:
        raise ValueError(f"Unknown capabilities: {', '.join(unknown)}")
    return {"server": server, "capabilities": sorted(capabilities), "version": version}
