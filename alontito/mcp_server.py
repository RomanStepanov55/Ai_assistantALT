"""Model Context Protocol (MCP) Server for Alontito.

Provides a standard JSON-RPC 2.0 interface for AI assistants (Cursor, Claude,
Antigravity, OpenCode, ZCode, etc.) to:
  - read the agent status, memory facts and measured runs
  - search the experience memory
  - record a learned fact into the agent's memory

HARD RULE #2 (user decision, 2026-11-24) and the 2026-11-27 pivot: the agent
never plays for the user and the game layer is gone. The former
execute_safe_action / vision / screenshot tools died with the Roblox code:
there is nothing to execute and nothing to look at.

Runs over stdio without external dependencies:
    python -m alontito.mcp_server
"""
import json
import sys
import threading
from typing import Any, Dict, List, Optional

from .memory import Memory, STATUS_SECONDARY

# Protocol constants
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "alontito-mcp"
SERVER_VERSION = "2.0.0"


class AlontitoMCPServer:
    """Zero-dependency Model Context Protocol server."""

    def __init__(self, memory: Optional[Memory] = None):
        self.memory = memory or Memory()
        self.is_active = False
        self._thread: Optional[threading.Thread] = None

    def get_server_info(self) -> Dict[str, Any]:
        return {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {
                "tools": {
                    "listChanged": True,
                },
                "resources": {
                    "subscribe": False,
                    "listChanged": False,
                },
            },
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_agent_status",
                "description": (
                    "Reads the agent status: sessions, facts count, open "
                    "questions, skills with measured rates, learning stats."
                ),
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_agent_memory",
                "description": (
                    "Retrieves the agent's learned facts (with evidence "
                    "statuses), open questions and measured skills."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search": {
                            "type": "string",
                            "description": "Optional substring to filter facts",
                        },
                    },
                },
            },
            {
                "name": "search_experience",
                "description": (
                    "Searches the experience memory (SQLite) for past "
                    "situations and lessons similar to the query. Returns an "
                    "empty list when the store does not exist yet."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "What to recall",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 4)",
                            "default": 4,
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "record_learned_fact",
                "description": (
                    "Records an insight or fact into Alontito's memory. The "
                    "fact is stored with status СООБЩАЕТСЯ ВТОРИЧНЫМ: an "
                    "external AI is a secondary source, not a measurement."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "Fact or insight text to remember",
                        },
                        "source": {
                            "type": "string",
                            "description": "Who reports this (default: mcp client name)",
                        },
                    },
                    "required": ["fact"],
                },
            },
        ]

    def list_resources(self) -> List[Dict[str, Any]]:
        return [
            {
                "uri": "alontito://memory/facts",
                "name": "Learned Facts & Insights",
                "description": "Persistent memory of what the agent learned",
                "mimeType": "application/json",
            },
        ]

    def _experience_memory(self):
        """Lazy import: the store may not exist on a fresh machine."""
        try:
            from brain.experience_memory import ExperienceMemory
            return ExperienceMemory()
        except Exception:
            return None

    def handle_call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an MCP tool call."""
        if tool_name == "get_agent_status":
            from .brain import Brain
            summary = Brain(self.memory).summary()
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }

        elif tool_name == "get_agent_memory":
            needle = (arguments.get("search") or "").strip()
            facts = self.memory.find_facts(needle) if needle else self.memory.facts()
            summary = {
                "facts": facts,
                "open_questions": self.memory.open_questions(),
                "skills": [
                    {"name": s.name, "attempts": s.attempts,
                     "successes": s.successes, "per_minute": s.per_minute}
                    for s in self.memory.skills()
                ],
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }

        elif tool_name == "search_experience":
            query = (arguments.get("query") or "").strip()
            if not query:
                return {"isError": True,
                        "content": [{"type": "text", "text": "Empty query."}]}
            store = self._experience_memory()
            if store is None:
                return {
                    "content": [{"type": "text",
                                 "text": "Опытов пока нет: база ещё не создана."}],
                }
            rows = store.recall(query, limit=int(arguments.get("limit", 4) or 4))
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(rows, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }

        elif tool_name == "record_learned_fact":
            fact = (arguments.get("fact") or "").strip()
            if not fact:
                return {"isError": True,
                        "content": [{"type": "text", "text": "Empty fact provided."}]}
            source = (arguments.get("source") or "mcp client").strip()[:100]
            stored = self.memory.add_fact(fact, STATUS_SECONDARY, source)
            return {
                "content": [
                    {"type": "text",
                     "text": f"Fact recorded (status: {stored['status']}, "
                             f"source: {source})."}
                ]
            }

        elif tool_name == "execute_safe_action":
            # HARD RULE #2 + pivot: no game action exists any more. The name
            # is kept in the dispatch only to answer honestly instead of
            # "Unknown tool".
            return {
                "isError": True,
                "content": [{"type": "text",
                             "text": "Действий в играх больше нет: игровой "
                                     "слой удалён из проекта (2026-11-27). "
                                     "Доступны память и опыт агента."}],
            }

        return {
            "isError": True,
            "content": [{"type": "text", "text": f"Unknown tool: '{tool_name}'"}],
        }

    def handle_request(self, req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Processes a single JSON-RPC 2.0 request according to MCP specification."""
        method = req.get("method")
        msg_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": True},
                        "resources": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                },
            }

        elif method == "notifications/initialized":
            # Client confirmed initialization
            return None

        elif method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": self.list_tools()},
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            args = params.get("arguments") or {}
            res = self.handle_call_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": res,
            }

        elif method == "resources/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"resources": self.list_resources()},
            }

        elif method == "resources/read":
            uri = params.get("uri")
            if uri == "alontito://memory/facts":
                facts = self.memory.facts()
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(facts, indent=2, ensure_ascii=False, default=str),
                            }
                        ]
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"Resource not found: {uri}"},
            }

        if msg_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return None

    def run_stdio_loop(self):
        """Runs standard I/O communication loop for CLI/Desktop client MCP attachment."""
        self.is_active = True
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = json.loads(line)
                except Exception as e:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {e}"},
                    }
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()
                    continue

                resp = self.handle_request(req)
                if resp is not None:
                    sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                    sys.stdout.flush()
        finally:
            self.is_active = False


def main():
    AlontitoMCPServer().run_stdio_loop()


if __name__ == "__main__":
    main()
