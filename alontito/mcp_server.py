"""Model Context Protocol (MCP) Server for Alontito / Roblox Agent.

Provides a standard JSON-RPC 2.0 interface for AI assistants (Cursor, Claude,
Antigravity, OpenCode, etc.) to:
  - inspect what the bot currently sees (HUD, badges, raw frame)
  - read memory, facts and learned skills
  - execute whitelisted safe actions in the game
  - control autonomous training / pause

Runs over stdio or local TCP/HTTP without external dependencies.
"""
import base64
import io
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from .memory import Memory
from .skills import AlontitoBot, ALLOWED_ACTIONS, guard_report

# Protocol constants
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "alontito-roblox-mcp"
SERVER_VERSION = "1.0.0"


class AlontitoMCPServer:
    """Zero-dependency Model Context Protocol server."""

    def __init__(self, bot: Optional[AlontitoBot] = None, memory: Optional[Memory] = None):
        self.bot = bot or AlontitoBot()
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
                "name": "get_game_vision_state",
                "description": (
                    "Reads current game vision state: Roblox window presence, "
                    "strength badge value, gems/candy badge, open modals, "
                    "readability confidence and scene warnings."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "force_capture": {
                            "type": "boolean",
                            "description": "True to force an immediate new screen grab",
                            "default": True,
                        }
                    },
                },
            },
            {
                "name": "capture_screenshot_base64",
                "description": (
                    "Captures the Roblox window or full desktop and returns "
                    "a PNG image encoded in base64. Ideal for visual AI reasoning."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "max_dimension": {
                            "type": "integer",
                            "description": "Max width or height to downscale for AI speed (default: 1024)",
                            "default": 1024,
                        }
                    },
                },
            },
            {
                "name": "execute_safe_action",
                "description": (
                    "Executes a verified safe game action. Permitted actions: "
                    + ", ".join(ALLOWED_ACTIONS)
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(ALLOWED_ACTIONS),
                            "description": "The whitelisted action name to execute",
                        },
                        "reps": {
                            "type": "integer",
                            "description": "Number of repetitions (for lifts/train, default 6)",
                            "default": 6,
                        },
                    },
                    "required": ["action"],
                },
            },
            {
                "name": "get_bot_memory",
                "description": "Retrieves the bot's learned facts, open questions and stats.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "record_learned_fact",
                "description": "Records an insight, strategy observation or game fact into Alontito's memory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "Fact or insight text to remember",
                        },
                        "game": {
                            "type": "string",
                            "description": "Target game name (default: current)",
                        },
                    },
                    "required": ["fact"],
                },
            },
        ]

    def list_resources(self) -> List[Dict[str, Any]]:
        return [
            {
                "uri": "alontito://vision/current_state",
                "name": "Current Vision State",
                "description": "Real-time JSON snapshot of HUD, badges and game window status",
                "mimeType": "application/json",
            },
            {
                "uri": "alontito://memory/facts",
                "name": "Learned Facts & Insights",
                "description": "Persistent memory of what the bot learned about games",
                "mimeType": "application/json",
            },
        ]

    def handle_call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches an MCP tool call."""
        if tool_name == "get_game_vision_state":
            force = arguments.get("force_capture", True)
            state = self.bot.inspect(force_fresh=force)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(state, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }

        elif tool_name == "capture_screenshot_base64":
            max_dim = int(arguments.get("max_dimension", 1024))
            img = self.bot.capture_frame()
            if img is None:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": "Failed to capture window (Roblox not found or obscured)."}],
                }
            # Resize if necessary to save bandwidth
            w, h = img.size
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                img = img.resize((int(w * scale), int(h * scale)))

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_str = base64.b64encode(buf.getvalue()).decode("ascii")

            return {
                "content": [
                    {
                        "type": "image",
                        "data": b64_str,
                        "mimeType": "image/png",
                    },
                    {
                        "type": "text",
                        "text": f"Screenshot captured successfully: {img.width}x{img.height} px.",
                    },
                ]
            }

        elif tool_name == "execute_safe_action":
            action = arguments.get("action")
            reps = int(arguments.get("reps", 6))
            if action not in ALLOWED_ACTIONS:
                return {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Action '{action}' is rejected by safety guards."}],
                }
            result = self.bot.run_action(action, reps=reps)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }

        elif tool_name == "get_bot_memory":
            facts = self.memory.facts()
            questions = self.memory.open_questions()
            skills = self.memory.skills()
            summary = {
                "selected_game": self.memory.selected_game(),
                "facts": facts,
                "open_questions": questions,
                "skills": skills,
            }
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(summary, indent=2, ensure_ascii=False, default=str),
                    }
                ]
            }

        elif tool_name == "record_learned_fact":
            fact = arguments.get("fact", "").strip()
            game = arguments.get("game") or (self.memory.selected_game() or {}).get("name", "Unknown")
            if not fact:
                return {"isError": True, "content": [{"type": "text", "text": "Empty fact provided."}]}
            self.memory.add_fact(fact, game=game)
            return {
                "content": [
                    {"type": "text", "text": f"Fact recorded: '{fact}' for game '{game}'."}
                ]
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
            if uri == "alontito://vision/current_state":
                state = self.bot.inspect(force_fresh=False)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "contents": [
                            {
                                "uri": uri,
                                "mimeType": "application/json",
                                "text": json.dumps(state, indent=2, ensure_ascii=False, default=str),
                            }
                        ]
                    },
                }
            elif uri == "alontito://memory/facts":
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
