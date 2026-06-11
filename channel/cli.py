#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import json
import unicodedata
import logging
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_servers, get_deploy_nodes, get_remote_dir
from services.npu_service import query_npu_status, fmt_list
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
    setup_docker,
    list_containers,
    auto_deploy,
    smart_deploy,
)

log = logging.getLogger("NPU-Tools.Channel.CLI")


def _display_width(text):
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _pad_right(text, width):
    current_width = _display_width(text)
    padding = width - current_width
    return text + ' ' * max(0, padding)


def _strip_ansi(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def _print_npu_table(results):
    success_results = [r for r in results if not r.get('error')]
    error_results = [r for r in results if r.get('error')]
    err_count = len(error_results)

    idle_all = 0
    busy_all = 0

    COL_HOST = 15
    COL_IDLE = 25
    COL_BUSY = 25
    COL_STATUS = 8
    SEP = "   "

    header_host = _pad_right('服务器', COL_HOST)
    header_idle = _pad_right('空闲卡', COL_IDLE)
    header_busy = _pad_right('占用卡', COL_BUSY)
    header_status = _pad_right('状态', COL_STATUS)

    total_width = _display_width(header_host) + len(SEP) + _display_width(header_idle) + len(SEP) + _display_width(header_busy) + len(SEP) + _display_width(header_status)

    print()
    print('=' * total_width)
    print(f"  {header_host}{SEP}{header_idle}{SEP}{header_busy}{SEP}{header_status}")
    print('-' * total_width)

    for r in success_results:
        host = r.get('host', 'unknown')
        parts = host.split('.')
        disp_host = f"192.168.25.{parts[-1]}" if len(parts) >= 4 else host

        ic = len(r.get('idle', []))
        bc = len(r.get('busy', []))
        idle_all += ic
        busy_all += bc

        idle_str = f"{ic}卡 ({fmt_list(r.get('idle', []))})"
        busy_str = f"{bc}卡 ({fmt_list(r.get('busy', []))})"

        if ic > 0:
            status_text = '有空闲'
            status = f'\033[32m{status_text}\033[0m'
        elif bc > 0:
            status_text = '全占用'
            status = f'\033[33m{status_text}\033[0m'
        else:
            status_text = '无卡'
            status = f'\033[90m{status_text}\033[0m'

        line = f"  {_pad_right(disp_host, COL_HOST)}{SEP}{_pad_right(idle_str, COL_IDLE)}{SEP}{_pad_right(busy_str, COL_BUSY)}{SEP}{_strip_ansi(status):<{COL_STATUS}}"
        print(line)

    print('-' * total_width)
    total_idle = f"{idle_all} 卡空闲"
    total_busy = f"{busy_all} 卡占用"
    space_for_idle = COL_IDLE - _display_width('卡空闲') - _display_width(str(idle_all)) - 1
    space_for_busy = COL_BUSY - _display_width('卡占用') - _display_width(str(busy_all)) - 1
    print(f"  {_pad_right('总计', COL_HOST)}{SEP}\033[32m{total_idle}\033[0m{' ' * max(0, space_for_idle)}{SEP}\033[33m{total_busy}\033[0m{' ' * max(0, space_for_busy)}")
    print('=' * total_width)

    if error_results:
        print(f"\n⚠  {err_count} 台服务器查询失败:", file=sys.stderr)
        for r in error_results:
            print(f"   - {r.get('host', 'unknown')}: {r['error']}", file=sys.stderr)
        print(file=sys.stderr)

    if err_count == len(results):
        sys.exit(3)
    elif idle_all == 0:
        sys.exit(2)
    elif err_count > 0:
        sys.exit(1)


def cmd_query(args):
    results = query_npu_status(args.hosts)
    if not results:
        print("没有匹配的服务器", file=sys.stderr)
        sys.exit(1)
    _print_npu_table(results)


def cmd_launch(args):
    result = launch_script(args.script, args.hosts, args.args)
    if not result['success']:
        print(f"❌ 启动失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n🚀 脚本启动: {result.get('results', [{}])[0].get('script', 'N/A')}")
    print(f"   成功: {result['success_count']}/{result['total']}\n")
    for r in result['results']:
        host = r.get('host', 'unknown')
        if r['success']:
            print(f"  ✅ {host}: PID={r['pid']}")
            print(f"     日志: {r['log_file']}")
        else:
            print(f"  ❌ {host}: {r.get('error', '未知错误')}")


def cmd_sync(args):
    result = sync_script(args.script, args.hosts)
    if not result['success']:
        print(f"❌ 同步失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n📤 脚本同步: {os.path.basename(args.script)}")
    print(f"   成功: {result['success_count']}/{result['total']}\n")
    for r in result['results']:
        host = r.get('host', 'unknown')
        if r['success']:
            print(f"  ✅ {host}: {r.get('remote_path', '')}")
        else:
            print(f"  ❌ {host}: {r.get('error', '未知错误')}")


def cmd_sync_dir(args):
    result = sync_directory(args.dir, args.hosts)
    if not result['success']:
        print(f"❌ 目录同步失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n📤 目录同步: {result['total_files']} 个文件 × {result['total_servers']} 台服务器")
    print(f"   成功: {result['success_count']}/{result['total_operations']}\n")
    for r in result['results']:
        host = r.get('host', 'unknown')
        f = r.get('file', 'N/A')
        if r['success']:
            print(f"  ✅ {host}: {f}")
        else:
            print(f"  ❌ {host}: {f} - {r.get('error', '未知错误')}")


def cmd_watch_start(args):
    result = start_watcher(args.hosts, args.interval)
    if not result['success']:
        print(f"❌ {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"👁 文件监控已启动: {result['message']}")
    print("   按 Ctrl+C 停止")
    try:
        import threading
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        cmd_watch_stop(None)


def cmd_watch_stop(args):
    result = stop_watcher()
    if not result['success']:
        print(f"❌ {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(f"✅ {result['message']}")


def cmd_log(args):
    result = get_script_log(args.log_file, args.hosts, args.lines)
    if not result['success']:
        print(f"❌ 读取日志失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n📋 日志文件: {result['log_file']} (最后 {result['lines']} 行)")
    print("=" * 60)
    for r in result['results']:
        host = r.get('host', 'unknown')
        output = r.get('output', '')
        if output:
            print(f"--- {host} ---")
            print(output)
        else:
            error = r.get('error', '')
            if error:
                print(f"--- {host}: 读取失败 - {error} ---")
            else:
                print(f"--- {host}: 暂无输出 ---")


def cmd_logs(args):
    result = list_logs(args.hosts, args.keyword)
    if not result['success']:
        print(f"❌ 列出日志失败", file=sys.stderr)
        sys.exit(1)

    keyword = result.get('keyword')
    title = f"日志过滤: {keyword}" if keyword else "所有日志文件"
    print(f"\n📂 {title}")
    print("=" * 60)
    for r in result['results']:
        host = r.get('host', 'unknown')
        log_files = r.get('log_files', [])
        print(f"--- {host} ({len(log_files)} 个文件) ---")
        if log_files:
            for lf in log_files:
                print(f"  {lf['date']}  {lf['size']:>8}  {lf['path']}")
        else:
            print("  无日志文件")


def cmd_ps(args):
    result = list_processes(args.hosts, args.keyword)
    if not result['success']:
        print(f"❌ 列出进程失败", file=sys.stderr)
        sys.exit(1)

    keyword = result.get('keyword')
    title = f"进程过滤: {keyword}" if keyword else "所有 Python3 进程"
    print(f"\n⚙ {title}")
    print("=" * 60)
    for r in result['results']:
        host = r.get('host', 'unknown')
        processes = r.get('processes', [])
        print(f"--- {host} ({len(processes)} 个进程) ---")
        if processes:
            for p in processes:
                print(f"  PID={p['pid']}  CPU={p['cpu']}%  MEM={p['mem']}%  {p['command']}")
        else:
            print("  无匹配进程")


def cmd_stop(args):
    result = stop_script(args.pid, args.hosts)
    if not result['success']:
        print(f"❌ 终止进程失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n🛑 终止进程 PID={args.pid}")
    print(f"   成功: {result['success_count']}/{result['total']}\n")
    for r in result['results']:
        host = r.get('host', 'unknown')
        if r.get('stopped'):
            print(f"  ✅ {host}: 已停止")
        else:
            print(f"  ❌ {host}: 进程不存在或无法终止")


def cmd_servers(args):
    servers = get_servers()
    deploy_nodes = get_deploy_nodes()
    remote_dir = get_remote_dir()

    print(f"\n🖥 配置的服务器 ({len(servers)} 台):")
    for s in servers:
        is_deploy = "🚀 部署节点" if s['host'] in deploy_nodes else "📊 监控节点"
        docker_info = ""
        if s.get('docker', {}).get('container'):
            docker_info = f" 🐳 {s['docker']['container']}"
        print(f"  {s['host']}:{s.get('port', 22)} ({s.get('username', 'root')}) {is_deploy}{docker_info}")
    print(f"\n远程部署目录: {remote_dir}")
    if deploy_nodes:
        print(f"部署节点: {', '.join(deploy_nodes)}")


def cmd_docker(args):
    result = setup_docker(args.hosts)
    if not result['success']:
        print(f"❌ Docker 设置失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n🐳 Docker 容器设置: 成功 {result['success_count']}/{result['total']}\n")
    for r in result['results']:
        host = r.get('host', 'unknown')
        action = r.get('action', '')
        container = r.get('container', '')
        if r['success']:
            if action == 'already_running':
                print(f"  ✅ {host}: 容器 {container} 已在运行")
            elif action == 'created':
                print(f"  ✅ {host}: 容器 {container} 已创建 (ID: {r.get('container_id', '')})")
            elif action == 'skipped':
                print(f"  ⏭ {host}: 未配置 Docker，跳过")
        else:
            print(f"  ❌ {host}: 创建失败 - {r.get('error', '未知错误')}")


def cmd_containers(args):
    result = list_containers(args.hosts)
    if not result['success']:
        print(f"❌ 列出容器失败", file=sys.stderr)
        sys.exit(1)

    print(f"\n🐳 Docker 容器状态:")
    print("=" * 60)
    for r in result['results']:
        host = r.get('host', 'unknown')
        containers = r.get('containers', [])
        print(f"--- {host} ({len(containers)} 个容器) ---")
        if containers:
            for c in containers:
                print(f"  {c['id']}  {c['name']}  {c['status']}  {c['image']}")
        else:
            print("  无容器")


def cmd_auto_deploy(args):
    result = auto_deploy(args.script, args.count, args.args, args.min_idle)
    if not result['success'] and not result.get('selected_hosts'):
        print(f"\n❌ 自动部署失败: {result.get('error', '未知错误')}", file=sys.stderr)
        sys.exit(1)

    print(f"\n🎯 自动选择节点: {result.get('actual_count', 0)}/{result.get('requested_count', args.count)}")
    print(f"   最低空闲卡要求: {result.get('min_idle_cards', args.min_idle)} 张")
    print()
    print("选中节点:")
    for s in result.get('selection_detail', []):
        idle_str = ','.join(map(str, s['idle_list']))
        print(f"  ✅ {s['host']}: {s['idle_cards']} 卡空闲 ({idle_str}) / 共 {s['total_cards']} 卡")

    if result.get('selected_hosts') and not result['success']:
        print(f"\n⚠ 节点已选择，但部署失败: {result.get('error', '')}", file=sys.stderr)
        sys.exit(1)

    launch_result = result.get('launch_result')
    if launch_result and launch_result.get('results'):
        print()
        print("启动详情:")
        for r in launch_result['results']:
            host = r.get('host', 'unknown')
            if r.get('success'):
                print(f"  ✅ {host}: PID={r.get('pid')}")
                print(f"     日志: {r.get('log_file', '')}")
            else:
                print(f"  ❌ {host}: {r.get('error', '未知错误')}")
        print()
        print("💡 使用 'log <log_file>' 查看日志输出")


def cmd_smart_deploy(args):
    result = smart_deploy(args.script, args.count, args.args, args.min_idle)
    print(f"\n🚀 智能部署全流程")
    print("=" * 60)

    if not result['success'] and not result.get('selected_hosts'):
        print(f"❌ 部署失败: {result.get('error', '未知错误')}", file=sys.stderr)
        sys.exit(1)

    for step in result.get('steps', []):
        step_name = step['step']
        message = step.get('message', '')
        ok = step.get('success', False)
        icon = '✅' if ok else '❌'

        if step_name == 'select_nodes':
            print(f"\n📋 步骤1: 选择空闲节点")
            print(f"   {icon} {message}")
            for s in step.get('detail', []):
                idle_str = ','.join(map(str, s['idle_list']))
                print(f"      {s['host']}: {s['idle_cards']} 卡空闲 ({idle_str})")

        elif step_name == 'setup_docker':
            print(f"\n🐳 步骤2: 创建 Docker 容器")
            print(f"   {icon} {message}")
            for r in step.get('detail', []):
                host = r.get('host', 'unknown')
                action = r.get('action', '')
                if r.get('success'):
                    if action == 'already_running':
                        print(f"      ✅ {host}: 容器已在运行")
                    elif action == 'created':
                        print(f"      ✅ {host}: 容器已创建 (ID: {r.get('container_id', '')})")
                    elif action == 'skipped':
                        print(f"      ⏭ {host}: 无 Docker 配置，宿主机模式")
                else:
                    print(f"      ❌ {host}: {r.get('error', '未知错误')}")

        elif step_name == 'sync_script':
            print(f"\n📤 步骤3: 同步脚本")
            print(f"   {icon} {message}")

        elif step_name == 'launch_script':
            print(f"\n🚀 步骤4: 启动脚本")
            print(f"   {icon} {message}")
            for r in step.get('detail', []):
                host = r.get('host', 'unknown')
                if r.get('success'):
                    print(f"      ✅ {host}: PID={r.get('pid')}, 日志={r.get('log_file', '')}")
                else:
                    print(f"      ❌ {host}: {r.get('error', '未知错误')}")

    if result['success']:
        print(f"\n✅ 部署完成! 选中 {result.get('actual_count', 0)} 个节点")
        print(f"💡 使用 'log <log_file>' 查看日志输出，'ps' 查看进程状态")
    else:
        print(f"\n⚠ 部分步骤失败，请检查上方详情", file=sys.stderr)
        sys.exit(1)


def build_parser():
    parser = argparse.ArgumentParser(
        prog='npu',
        description='NPU Tools - 多节点 NPU 集群管理工具'
    )
    sub = parser.add_subparsers(dest='command', help='可用命令')

    p_query = sub.add_parser('query', help='查询 NPU 状态')
    p_query.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')

    p_launch = sub.add_parser('launch', help='后台启动脚本')
    p_launch.add_argument('script', help='脚本文件名或本地路径')
    p_launch.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')
    p_launch.add_argument('--args', default='', help='传递给脚本的参数')

    p_sync = sub.add_parser('sync', help='同步单个脚本')
    p_sync.add_argument('script', help='本地脚本路径')
    p_sync.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')

    p_sync_dir = sub.add_parser('sync-dir', help='批量同步目录')
    p_sync_dir.add_argument('--dir', help='本地目录路径')
    p_sync_dir.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')

    p_watch_start = sub.add_parser('watch', help='启动文件监控')
    p_watch_start.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')
    p_watch_start.add_argument('--interval', type=int, default=2, help='检查间隔(秒)')

    p_watch_stop = sub.add_parser('watch-stop', help='停止文件监控')

    p_log = sub.add_parser('log', help='查看脚本运行日志')
    p_log.add_argument('log_file', help='远程日志文件路径')
    p_log.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')
    p_log.add_argument('--lines', type=int, default=50, help='显示最后 N 行')

    p_logs = sub.add_parser('logs', help='列出日志文件')
    p_logs.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')
    p_logs.add_argument('--keyword', help='按脚本名筛选')

    p_ps = sub.add_parser('ps', help='列出运行中的 Python 进程')
    p_ps.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')
    p_ps.add_argument('--keyword', help='按关键字筛选进程')

    p_stop = sub.add_parser('stop', help='终止进程')
    p_stop.add_argument('pid', help='进程 PID')
    p_stop.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')

    p_servers = sub.add_parser('servers', help='列出配置的服务器')

    p_docker = sub.add_parser('docker', help='创建/启动 Docker 容器')
    p_docker.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')

    p_containers = sub.add_parser('containers', help='列出 Docker 容器状态')
    p_containers.add_argument('--hosts', nargs='+', help='目标服务器 IP 列表')

    p_auto = sub.add_parser('auto', help='自动选择空闲节点部署脚本')
    p_auto.add_argument('script', help='脚本文件名或本地路径')
    p_auto.add_argument('--count', type=int, default=4, help='选择节点数量（默认 4）')
    p_auto.add_argument('--min-idle', type=int, default=1, help='每节点最少空闲卡数（默认 1）')
    p_auto.add_argument('--args', default='', help='传递给脚本的参数')

    p_smart = sub.add_parser('deploy', help='一键智能部署（选节点+Docker+同步+启动）')
    p_smart.add_argument('script', help='脚本文件名或本地路径')
    p_smart.add_argument('--count', type=int, default=4, help='选择节点数量（默认 4）')
    p_smart.add_argument('--min-idle', type=int, default=1, help='每节点最少空闲卡数（默认 1）')
    p_smart.add_argument('--args', default='', help='传递给脚本的参数')

    return parser


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        cmd_query(args)
        return

    dispatch = {
        'query': cmd_query,
        'launch': cmd_launch,
        'sync': cmd_sync,
        'sync-dir': cmd_sync_dir,
        'watch': cmd_watch_start,
        'watch-stop': cmd_watch_stop,
        'log': cmd_log,
        'logs': cmd_logs,
        'ps': cmd_ps,
        'stop': cmd_stop,
        'servers': cmd_servers,
        'docker': cmd_docker,
        'containers': cmd_containers,
        'auto': cmd_auto_deploy,
        'deploy': cmd_smart_deploy,
    }

    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
