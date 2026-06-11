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
    get_script_log,
    list_logs,
    list_processes,
    stop_script,
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


def _format_launch_result(result):
    lines = []
    lines.append(f"启动脚本: {result.get('results', [{}])[0].get('script', 'N/A')}")
    lines.append(f"成功: {result.get('success_count', 'N/A')}/{result.get('total', 'N/A')}")
    lines.append("")
    lines.append("各节点详情:")
    for r in result.get('results', []):
        host = r.get('host', 'unknown')
        pid = r.get('pid')
        log_file = r.get('log_file', '')
        if r.get('success'):
            lines.append(f"  ✅ {host}: PID={pid}")
            lines.append(f"     日志: {log_file}")
        else:
            error = r.get('error', '未知错误')
            lines.append(f"  ❌ {host}: 失败 - {error}")
    lines.append("")
    lines.append("💡 使用 get_script_log 查看日志输出，使用 list_processes 查看运行中的进程")
    return '\n'.join(lines)


def _format_log_result(result):
    lines = []
    lines.append(f"日志文件: {result.get('log_file', 'N/A')}")
    lines.append(f"显示最后 {result.get('lines', 'N/A')} 行")
    lines.append("=" * 60)
    for r in result.get('results', []):
        host = r.get('host', 'unknown')
        output = r.get('output', '')
        if output:
            lines.append(f"--- {host} ---")
            lines.append(output)
        else:
            error = r.get('error', '')
            if error:
                lines.append(f"--- {host}: 读取失败 - {error} ---")
            else:
                lines.append(f"--- {host}: 暂无输出 ---")
    return '\n'.join(lines)


def _format_processes_result(result):
    lines = []
    keyword = result.get('keyword')
    if keyword:
        lines.append(f"进程过滤: {keyword}")
    else:
        lines.append("所有 Python3 进程")
    lines.append("=" * 60)
    for r in result.get('results', []):
        host = r.get('host', 'unknown')
        processes = r.get('processes', [])
        lines.append(f"--- {host} ({len(processes)} 个进程) ---")
        if processes:
            for p in processes:
                lines.append(f"  PID={p['pid']}  CPU={p['cpu']}%  MEM={p['mem']}%  {p['command']}")
        else:
            lines.append("  无匹配进程")
    return '\n'.join(lines)


def _format_list_logs_result(result):
    lines = []
    keyword = result.get('keyword')
    if keyword:
        lines.append(f"日志过滤: {keyword}")
    else:
        lines.append("所有日志文件")
    lines.append("=" * 60)
    for r in result.get('results', []):
        host = r.get('host', 'unknown')
        log_files = r.get('log_files', [])
        lines.append(f"--- {host} ({len(log_files)} 个文件) ---")
        if log_files:
            for lf in log_files:
                lines.append(f"  {lf['date']}  {lf['size']:>8}  {lf['path']}")
        else:
            lines.append("  无日志文件")
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
            description="在指定节点上后台启动脚本（nohup），输出重定向到远程日志文件。返回进程 PID 和日志文件路径，可用 get_script_log 查看输出。",
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
        Tool(
            name="get_script_log",
            description="查看远程节点上脚本的运行日志。使用 launch_script 启动脚本后会返回日志文件路径，用此工具查看输出。",
            inputSchema={
                "type": "object",
                "properties": {
                    "log_file": {
                        "type": "string",
                        "description": "远程日志文件路径（launch_script 返回的 log_file 字段）"
                    },
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    },
                    "lines": {
                        "type": "integer",
                        "description": "显示最后 N 行日志",
                        "default": 50
                    }
                },
                "required": ["log_file"]
            }
        ),
        Tool(
            name="list_logs",
            description="列出远程节点上的日志文件。支持按脚本名筛选，按时间倒序排列。",
            inputSchema={
                "type": "object",
                "properties": {
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "日志文件名过滤关键字（如脚本名 'train'），为空则列出所有日志"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="list_processes",
            description="列出远程节点上正在运行的 Python 进程。可按关键字筛选。",
            inputSchema={
                "type": "object",
                "properties": {
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    },
                    "keyword": {
                        "type": "string",
                        "description": "进程过滤关键字（如脚本名 'train.py'），为空则列出所有 Python 进程"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="stop_script",
            description="终止远程节点上指定 PID 的进程。",
            inputSchema={
                "type": "object",
                "properties": {
                    "pid": {
                        "type": "string",
                        "description": "要终止的进程 PID（可通过 list_processes 获取）"
                    },
                    "hosts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标服务器 IP 列表，为空则使用配置中的 deploy_nodes"
                    }
                },
                "required": ["pid"]
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
            result = launch_script(script_path, hosts, args)
            return [TextContent(type="text", text=_format_launch_result(result))]

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

        elif name == "get_script_log":
            log_file = arguments['log_file']
            hosts = arguments.get('hosts')
            lines_count = arguments.get('lines', 50)
            result = get_script_log(log_file, hosts, lines_count)
            return [TextContent(type="text", text=_format_log_result(result))]

        elif name == "list_logs":
            hosts = arguments.get('hosts')
            keyword = arguments.get('keyword')
            result = list_logs(hosts, keyword)
            return [TextContent(type="text", text=_format_list_logs_result(result))]

        elif name == "list_processes":
            hosts = arguments.get('hosts')
            keyword = arguments.get('keyword')
            result = list_processes(hosts, keyword)
            return [TextContent(type="text", text=_format_processes_result(result))]

        elif name == "stop_script":
            pid = arguments['pid']
            hosts = arguments.get('hosts')
            result = stop_script(pid, hosts)
            return [TextContent(type="text", text=_format_results(result))]

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
