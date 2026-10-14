"""Keep the linked MCP reference synchronized with FastMCP's registry."""

from __future__ import annotations

import asyncio
import re
import tempfile
from pathlib import Path

from filingcopilot.mcp_server import create_mcp
from filingcopilot.service import FilingCopilot
from filingcopilot.settings import Settings

START = "<!-- MCP_TOOL_LIST_BEGIN -->"
END = "<!-- MCP_TOOL_LIST_END -->"


def documented_tools(readme: Path) -> set[str]:
    text = readme.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise AssertionError("MCP reference is missing tool list markers")
    section = text.split(START, 1)[1].split(END, 1)[0]
    return set(re.findall(r"`([a-z][a-z0-9_]*)`", section))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="filingcopilot-mcp-check-") as folder:
        service = FilingCopilot(Settings(home=Path(folder), embedding_mode="off"))
        actual = {tool.name for tool in asyncio.run(create_mcp(service).list_tools())}
    expected = documented_tools(Path("docs/mcp-tools.md"))
    if actual != expected:
        raise SystemExit(
            "MCP documentation registry mismatch: "
            f"missing={sorted(actual - expected)}, extra={sorted(expected - actual)}"
        )
    print(f"MCP documentation tool list passed: {len(actual)} tools")


if __name__ == "__main__":
    main()
