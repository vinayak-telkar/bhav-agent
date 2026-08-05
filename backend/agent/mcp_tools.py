"""
Shared helper for calling an MCP tool and getting back plain Python data,
used by digest_graph.py's structural (graph-decided, not LLM-decided) tool
calls, and by tests exercising the same tool surface.

Confirmed in the M0 spike (specs/02_langgraph_mcp_spike.md) and re-confirmed
while building the MCP server (specs/06): BaseTool.ainvoke() on an MCP-backed
tool behaves differently depending on the tool's return type —
- dict-returning tools: content is a single content block, no artifact.
- list-returning tools: content is split into one block per list element,
  and the *artifact* (populated only when the tool is invoked with the full
  ToolCall-dict form, not a bare args dict) carries
  `artifact["structured_content"]["result"]` — the clean, already-typed list.
This helper handles both without the caller needing to know which shape a
given tool returns.
"""
import json

from langchain_core.tools import BaseTool


async def call_tool(tool: BaseTool, **kwargs) -> dict | list:
    """Invokes an MCP-backed LangChain tool and returns its structured payload
    (a dict or a list of dicts) rather than LangChain's message content-block
    wrapper."""
    message = await tool.ainvoke(
        {"name": tool.name, "args": kwargs, "id": f"call_{tool.name}", "type": "tool_call"}
    )
    if message.artifact and "structured_content" in message.artifact:
        return message.artifact["structured_content"]["result"]

    block = message.content[0] if isinstance(message.content, list) else message.content
    text = block["text"] if isinstance(block, dict) else block
    return json.loads(text)
