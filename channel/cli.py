#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
import unicodedata
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import get_servers
from services.npu_service import query_npu_status, fmt_list

log = logging.getLogger("NPU-Tools.Channel.CLI")


def local_mode():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    servers = get_servers()
    if not servers:
        print("服务器列表为空", file=sys.stderr)
        sys.exit(1)

    log.info("=== 本地模式 ===")
    results = query_npu_status()

    success_results = [r for r in results if not r.get('error')]
    error_results = [r for r in results if r.get('error')]
    err_count = len(error_results)

    idle_all = 0
    busy_all = 0

    def display_width(text):
        width = 0
        for char in text:
            if unicodedata.east_asian_width(char) in ('F', 'W'):
                width += 2
            else:
                width += 1
        return width

    def pad_right(text, width):
        current_width = display_width(text)
        padding = width - current_width
        return text + ' ' * max(0, padding)

    def strip_ansi(text):
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        return ansi_escape.sub('', text)

    print()
    COL_HOST = 15
    COL_IDLE = 25
    COL_BUSY = 25
    COL_STATUS = 8
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
            status_text = '有空闲'
            status = f'\033[32m{status_text}\033[0m'
        elif bc > 0:
            status_text = '全占用'
            status = f'\033[33m{status_text}\033[0m'
        else:
            status_text = '无卡'
            status = f'\033[90m{status_text}\033[0m'

        line = f"  {pad_right(disp_host, COL_HOST)}{SEP}{pad_right(idle_str, COL_IDLE)}{SEP}{pad_right(busy_str, COL_BUSY)}{SEP}{strip_ansi(status):<{COL_STATUS}}"
        print(line)

    print('-' * total_width)
    total_idle = f"{idle_all} 卡空闲"
    total_busy = f"{busy_all} 卡占用"
    space_for_idle = COL_IDLE - display_width('卡空闲') - display_width(str(idle_all)) - 1
    space_for_busy = COL_BUSY - display_width('卡占用') - display_width(str(busy_all)) - 1
    print(f"  {pad_right('总计', COL_HOST)}{SEP}\033[32m{total_idle}\033[0m{' ' * max(0, space_for_idle)}{SEP}\033[33m{total_busy}\033[0m{' ' * max(0, space_for_busy)}")
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
    else:
        sys.exit(0)


if __name__ == '__main__':
    local_mode()
