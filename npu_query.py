#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NPU 状态一键查询脚本
用法: python npu_query.py [--local]
依赖: pip install paramiko pyyaml
"""

import os
import sys
import re
import time
import yaml
import unicodedata
import argparse
import logging
import concurrent.futures

try:
    import paramiko
except ImportError:
    print("缺少依赖: pip install paramiko pyyaml", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("NPU-Query")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, 'config.yaml')
MAX_RETRIES = 1


def load_servers():
    if not os.path.exists(CONFIG_PATH):
        print(f"配置文件不存在: {CONFIG_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    servers = config.get('servers', [])
    if not servers:
        print("服务器列表为空", file=sys.stderr)
        sys.exit(1)
    return servers


def ssh_exec(server, command):
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=server['host'],
            port=server.get('port', 22),
            username=server.get('username', 'root'),
            password=server.get('password', ''),
            timeout=5,
            look_for_keys=False,
            allow_agent=False
        )
        _, stdout, _ = client.exec_command(command, timeout=10)
        output = stdout.read().decode('utf-8', errors='replace').strip()
        client.close()
        return output
    except Exception as e:
        return f'Error: {e}'


def parse_npu_output(output):
    if not output or output.startswith('Error:'):
        return {'idle': [], 'busy': [], 'error': output or '无输出', 'total': 0}
    lines = output.split('\n')
    idle = set()
    busy = set()
    in_process_table = False
    for line in lines:
        stripped = line.strip()
        if 'NPU' in stripped and 'Chip' in stripped and 'Process id' in stripped:
            in_process_table = True
            continue
        m = re.search(r'No running processes found in NPU\s+(\d+)', stripped)
        if m:
            idle.add(int(m.group(1)))
            continue
        if in_process_table:
            m = re.search(r'\|\s*(\d+)\s+\d+\s*\|\s*(\d+)', stripped)
            if m:
                npu_id = int(m.group(1))
                pid = int(m.group(2))
                if pid > 0:
                    busy.add(npu_id)
    all_ids = idle | busy
    total_cards = max(all_ids) + 1 if all_ids else 0
    idle = sorted(idle - busy)
    return {'idle': idle, 'busy': sorted(busy), 'error': None, 'total': total_cards}


def check_server(server, max_retries=MAX_RETRIES):
    host = server['host']
    for attempt in range(max_retries):
        output = ssh_exec(server, 'npu-smi info')
        result = parse_npu_output(output)
        result['host'] = host
        if result['error']:
            log.warning(f"服务器 {host} 查询失败 (尝试 {attempt + 1}/{max_retries}): {result['error']}")
            time.sleep(1)
            continue
        return result
    if 'host' not in result:
        result['host'] = host
    return result


def fmt_list(lst):
    if not lst:
        return '-'
    if len(lst) > 8:
        return f'{",".join(map(str, lst[:3]))}...{",".join(map(str, lst[-2:]))}'
    return ','.join(map(str, lst))


def display_width(text):
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def pad_right(text, width):
    return text + ' ' * max(0, width - display_width(text))


def strip_ansi(text):
    return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', text)


def main():
    parser = argparse.ArgumentParser(description='NPU 状态一键查询')
    parser.add_argument('--local', action='store_true', help='本地模式（默认）')
    args = parser.parse_args()

    servers = load_servers()
    log.info("=== NPU 状态查询 ===")

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(servers)) as executor:
        futures = {executor.submit(check_server, s): s for s in servers}
        results = []
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result(timeout=15))
            except Exception as e:
                results.append({'host': futures[f]['host'], 'idle': [], 'busy': [], 'error': str(e), 'total': 0})

    success_results = [r for r in results if not r.get('error')]
    error_results = [r for r in results if r.get('error')]
    err_count = len(error_results)
    idle_all = 0
    busy_all = 0

    print()
    COL_HOST, COL_IDLE, COL_BUSY, COL_STATUS = 15, 25, 25, 8
    SEP = "   "

    header_host = pad_right('服务器', COL_HOST)
    header_idle = pad_right('空闲卡', COL_IDLE)
    header_busy = pad_right('占用卡', COL_BUSY)
    header_status = pad_right('状态', COL_STATUS)
    total_width = display_width(header_host) + len(SEP) + display_width(header_idle) + len(SEP) + display_width(header_busy) + len(SEP) + display_width(header_status)

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
            status = f'\033[32m有空闲\033[0m'
        elif bc > 0:
            status = f'\033[33m全占用\033[0m'
        else:
            status = f'\033[90m无卡\033[0m'
        print(f"  {pad_right(disp_host, COL_HOST)}{SEP}{pad_right(idle_str, COL_IDLE)}{SEP}{pad_right(busy_str, COL_BUSY)}{SEP}{strip_ansi(status):<{COL_STATUS}}")

    print('-' * total_width)
    space_idle = COL_IDLE - display_width('卡空闲') - display_width(str(idle_all)) - 1
    space_busy = COL_BUSY - display_width('卡占用') - display_width(str(busy_all)) - 1
    print(f"  {pad_right('总计', COL_HOST)}{SEP}\033[32m{idle_all} 卡空闲\033[0m{' ' * max(0, space_idle)}{SEP}\033[33m{busy_all} 卡占用\033[0m{' ' * max(0, space_busy)}")
    print('=' * total_width)

    if error_results:
        print(f"\n⚠  {err_count} 台服务器查询失败:", file=sys.stderr)
        for r in error_results:
            print(f"   - {r.get('host', 'unknown')}: {r['error']}", file=sys.stderr)

    if err_count == len(results):
        sys.exit(3)
    elif idle_all == 0:
        sys.exit(2)
    elif err_count > 0:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == '__main__':
    main()
