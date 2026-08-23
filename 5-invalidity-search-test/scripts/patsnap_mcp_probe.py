#!/usr/bin/env python3
"""Probe the Patsnap streamable-HTTP MCP endpoint.

Reads INVALIDITY_TEST_PATSNAP_MCP_URL from .env.test.local (never prints the
URL or key), performs the MCP initialize handshake, lists tools, and
optionally calls one tool with JSON arguments.  Output is truncated and the
endpoint is redacted so no credential reaches logs or reports.

用法:
  .venv/bin/python scripts/patsnap_mcp_probe.py --list-tools
  .venv/bin/python scripts/patsnap_mcp_probe.py --call <tool_name> --args '{"pn":"CN118474599A"}'
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx
from dotenv import dotenv_values

PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "invalidity-mcp-probe", "version": "0.1.0"}
MAX_OUTPUT_CHARS = 6000


class McpProbeError(RuntimeError):
    pass


def _parse_sse_or_json(response: httpx.Response) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" in content_type:
        messages: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            messages.append(json.loads(payload))
        if not messages:
            raise McpProbeError("SSE 响应中没有 data 消息")
        # 返回最后一个带 id 的 JSON-RPC 响应，否则返回最后一条消息。
        for message in reversed(messages):
            if "id" in message or "error" in message:
                return message
        return messages[-1]
    try:
        return response.json()
    except json.JSONDecodeError as exc:
        raise McpProbeError(
            f"响应不是 JSON/SSE: status={response.status_code} "
            f"content-type={content_type} body={response.text[:200]!r}"
        ) from exc


class McpStreamableClient:
    def __init__(self, url: str, *, timeout: float = 60.0) -> None:
        self._url = url
        self._timeout = timeout
        self._session_id: str | None = None
        self._next_id = 0
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "McpStreamableClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = self._client.post(self._url, json=payload, headers=headers)
        session_header = response.headers.get("mcp-session-id")
        if session_header:
            self._session_id = session_header
        if response.status_code == 202:
            return None  # notification accepted, no body
        if response.status_code >= 400:
            raise McpProbeError(
                f"MCP HTTP {response.status_code}: {response.text[:300]!r}"
            )
        return _parse_sse_or_json(response)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        result = self._post(payload)
        if result is None:
            raise McpProbeError(f"{method} 没有收到响应")
        if "error" in result:
            raise McpProbeError(f"{method} JSON-RPC 错误: {result['error']}")
        return result.get("result") or {}

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor
            result = self.request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.request("tools/call", {"name": name, "arguments": arguments})


def _redacted_dump(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n... [truncated]"
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.test.local")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--call", metavar="TOOL_NAME")
    parser.add_argument("--args", metavar="JSON", default="{}")
    args = parser.parse_args()

    env = {k: v for k, v in dotenv_values(args.env_file).items() if v}
    url = env.get("INVALIDITY_TEST_PATSNAP_MCP_URL", "").strip()
    if not url:
        raise SystemExit("配置缺少 INVALIDITY_TEST_PATSNAP_MCP_URL")
    if not url.startswith("https://"):
        raise SystemExit("MCP URL 必须使用 HTTPS")

    with McpStreamableClient(url) as client:
        init = client.initialize()
        server = init.get("serverInfo") or {}
        print(f"server: {server.get('name', '?')} {server.get('version', '')}")
        print(f"protocol: {init.get('protocolVersion', '?')}")
        capabilities = sorted((init.get("capabilities") or {}).keys())
        print(f"capabilities: {capabilities}")

        if args.list_tools or not args.call:
            tools = client.list_tools()
            print(f"\ntools ({len(tools)}):")
            for tool in tools:
                description = (tool.get("description") or "").splitlines()[0][:120]
                print(f"  - {tool.get('name')}: {description}")
                schema = tool.get("inputSchema") or {}
                props = schema.get("properties") or {}
                if props:
                    required = set(schema.get("required") or [])
                    summary = ", ".join(
                        f"{name}{'*' if name in required else ''}"
                        for name in list(props)[:10]
                    )
                    print(f"    args: {summary}")

        if args.call:
            arguments = json.loads(args.args)
            if not isinstance(arguments, dict):
                raise SystemExit("--args 必须是 JSON 对象")
            print(f"\ncall {args.call}:")
            result = client.call_tool(args.call, arguments)
            print(_redacted_dump(result))

    return 0


if __name__ == "__main__":
    sys.exit(main())
