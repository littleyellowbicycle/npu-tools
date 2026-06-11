#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NPU 工具入口（向后兼容）
  --local  : 本地模式，查询 NPU 状态并输出到 stdout
  默认     : 启动飞书 WebSocket 机器人
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser(
        description='NPU 工具入口',
        epilog='示例:\n'
               '  python npu_status_query.py             # 启动飞书 WebSocket 机器人\n'
               '  python npu_status_query.py --local      # 本地模式：查询并输出到 stdout',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--local',
        action='store_true',
        default=False,
        help='本地模式：查询所有服务器 NPU 状态并输出到 stdout，不连接飞书',
    )
    args = parser.parse_args()

    if args.local:
        from channel.cli import local_mode
        local_mode()
    else:
        from channel.feishu import main as feishu_main
        feishu_main()


if __name__ == '__main__':
    main()
