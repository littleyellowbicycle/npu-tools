#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import logging
import asyncio
from typing import Any

from mcp.server.models import InitializationOptions
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import filter_servers, get_servers, get_deploy_nodes, get_remote_dir
from services.npu_service import query_npu_status, format_status_text
from services.develop import (
    launch_script,
    sync_script,
    sync_directory,
    start_watcher,
    stop_watcher,
)

log = logging.getLogger("NPU-Tools.Channel.MCP")

server = Server("npu-tools")


def _format_results(result):
    if not result.get('results'):
        return json.dumps(result, ensure_ascii=False, indent=2)

    lines = []
    lines.append(f"总操作数: {result.get('total', 'N/A')}")
    lines.append(f"成功: {result.get('success_count', 'N/A')}")
    lines.append(f"失败: {result.get('fail_count', 'N/A')}")
    lines.append("")
    lines.append("各节点详情:")
    for r in result['results']:
        host = r.get('host', 'unknown')
        if r.get('success'):
            lines.append(f"  ✅ {host}: 成功")
            output = r.get('output', '')
            if output:
                for line in output.split('\n')[:20]:
                    lines.append(f"     {line}")
                if output.count('\n') > 20:
                    lines.append(f"     ... (省略 {output.count(chr(10)) - 20} 行)")
        else:
            error = r.get('error', '未知错误')
            lines.append(f"  ❌ {host}: 失败 - {error}")
    return '\n'.join(lines)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="query_npu_status",
            description="查询指定节点（或全部节点）的 NPU 状态。返回每台服务器的空闲/占用 NPU 卡信息。",
            inputSchema={
                "type": "object",
                "properties": {
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "要查询的服务器 IP 列表，为空则查询所有配置的服务器"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="launch_script",
            description="在指定节点上远程启动同一个脚本。脚本需已存在于远程节点的部署目录中。",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": "要启动的脚本文件名（如 'train.py'），或本地完整路径"
                    },
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    },
                    "args": {
                        "type": "string",
                        "description": "传递给脚本的命令行参数",
                        "default": ""
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "命令执行超时时间（秒）",
                        "default": 30
                    }
                },
                "required": ["script_path"]
            }
        ),
        Tool(
            name="sync_script",
            description="将本地脚本同步到指定节点的远程目录。支持单个文件同步。",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": "本地脚本文件的完整路径"
                    },
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    }
                },
                "required": ["script_path"]
            }
        ),
        Tool(
            name="sync_directory",
            description="将本地目录下所有 Python 脚本同步到指定节点的远程目录。",
            inputSchema={
                "type": "object",
                "properties": {
                    "local_dir": {
                        "type": "string",
                        "description": "本地目录路径，为空则使用配置中的 watch_dir"
                    },
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="start_file_watcher",
            description="启动文件监控，当本地脚本被修改时自动同步到指定节点。",
            inputSchema={
                "type": "object",
                "properties": {
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    },
                    "interval": {
                        "type": "integer",
                        "description": "文件检查间隔（秒）",
                        "default": 2
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="stop_file_watcher",
            description="停止文件监控。",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        Tool(
            name="list_servers",
            description="列出配置中所有可用的服务器节点信息。",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        if name == "query_npu_status":
            hosts = arguments.get('hosts')
            results = query_npu_status(hosts)
            if not results:
                return [TextContent(type="text", text="没有匹配的服务器")]
            text = format_status_text(results)
            return [TextContent(type="text", text=text)]

        elif name == "launch_script":
            script_path = arguments['script_path']
            hosts = arguments.get('hosts')
            args = arguments.get('args', '')
            timeout = arguments.get('timeout', 30)
            result = launch_script(script_path, hosts, args, timeout)
            return [TextContent(type="text", text=_format_results(result))]

        elif name == "sync_script":
            script_path = arguments['script_path']
            hosts = arguments.get('hosts')
            result = sync_script(script_path, hosts)
            return [TextContent(type="text", text=_format_results(result))]

        elif name == "sync_directory":
            local_dir = arguments.get('local_dir')
            hosts = arguments.get('hosts')
            result = sync_directory(local_dir, hosts)
            return [TextContent(type="text", text=_format_results(result))]

        elif name == "start_file_watcher":
            hosts = arguments.get('hosts')
            interval = arguments.get('interval', 2)
            result = start_watcher(hosts, interval)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

        elif name == "stop_file_watcher":
            result = stop_watcher()
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

        elif name == "list_servers":
            servers = get_servers()
            deploy_nodes = get_deploy_nodes()
            remote_dir = get_remote_dir()

            lines = [f"配置的服务器 ({len(servers)} 台):"]
            for s in servers:
                is_deploy = "🚀 部署节点" if s['host'] in deploy_nodes else "📊 监控节点"
                lines.append(f"  {s['host']}:{s.get('port', 22)} ({s.get('username', 'root')}) {is_deploy}")
            lines.append(f"\n远程部署目录: {remote_dir}")
            if deploy_nodes:
                lines.append(f"部署节点: {', '.join(deploy_nodes)}")

            return [TextContent(type="text", text='\n'.join(lines))]

        else:
            return [TextContent(type="text", text=f"未知工具: {name}")]

    except Exception as e:
        log.error(f"工具调用异常: {e}")
        return [TextContent(type="text", text=f"错误: {str(e)}")]


async def _main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="npu-tools",
                server_version="1.0.0",
            ),
        )


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    asyncio.run(_main())


if __name__ == '__main__':
    main()
