"""Shared safety layer for every MCP server: response envelope, limits, path guard, time windows, auth.

This package must never import from `app/`: the MCP servers run as separate processes and must not pull
in the platform (its settings, its database, its secrets).
"""
