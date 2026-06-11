#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import logging
import concurrent.futures

from config import filter_servers
from driver.ssh_driver import exec_command

log = logging.getLogger("NPU-Tools.Service.NPU")


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

    return {
        'idle': idle,
        'busy': sorted(busy),
        'error': None,
        'total': total_cards
    }


def query_npu_status(hosts=None, max_retries=1):
    servers = filter_servers(hosts)
    if not servers:
        return []

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(servers)) as executor:
        futures = {}
        for s in servers:
            f = executor.submit(_query_single_server, s, max_retries)
            futures[f] = s

        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result(timeout=15))
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'idle': [],
                    'busy': [],
                    'error': str(e),
                    'total': 0
                })

    return results


def _query_single_server(server, max_retries=1):
    host = server['host']
    result = None

    for attempt in range(max_retries):
        raw = exec_command(server, 'npu-smi info')
        output = raw['output'] if raw['success'] else f"Error: {raw['error']}"
        result = parse_npu_output(output)
        result['host'] = host

        if result['error']:
            log.warning(f"服务器 {host} 查询失败 (尝试 {attempt + 1}/{max_retries}): {result['error']}")
            continue

        log.info(f"服务器 {host} 查询成功: 总卡数={result['total']}, "
                f"空闲={len(result['idle'])}, 占用={len(result['busy'])}")
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


def generate_status_image(results, path='/tmp/npu_status.png'):
    from PIL import Image, ImageDraw, ImageFont

    WIDTH = 760
    PAD = 25
    HDR_H = 50
    ROW_H = 40
    FTR_H = 42
    TITLE_H = 38
    total_rows = len(results)
    total_h = PAD + TITLE_H + HDR_H + total_rows * ROW_H + FTR_H + PAD
    HEIGHT = total_h

    C_BG = (255, 255, 255)
    C_HDR = (230, 245, 255)
    C_FTR = (230, 245, 255)
    C_ROW1 = (255, 255, 255)
    C_ROW2 = (247, 248, 250)
    C_BORDER = (200, 210, 220)
    C_TEXT = (55, 55, 55)
    C_GREEN = (34, 150, 50)
    C_ORANGE = (220, 110, 15)
    C_RED = (210, 50, 50)
    C_GRAY = (140, 140, 140)

    font = font_b = font_t = None
    font_paths = [
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 14)
                font_b = ImageFont.truetype(fp, 15)
                font_t = ImageFont.truetype(fp, 20)
                log.info(f"使用字体: {fp}")
                break
            except Exception as e:
                log.warning(f"字体加载失败: {fp} - {e}")
    if not font:
        font = font_b = font_t = ImageFont.load_default()
        log.warning("使用默认字体，可能显示乱码")

    img = Image.new('RGB', (WIDTH, HEIGHT), C_BG)
    d = ImageDraw.Draw(img)

    y = PAD
    d.text((PAD, y), "📊 NPU 空闲状态", fill=C_TEXT, font=font_t)
    y += TITLE_H

    d.rectangle([PAD, y, WIDTH - PAD, y + HDR_H], fill=C_HDR, outline=C_BORDER, width=2)
    cols = [
        (PAD + 15, "服务器"),
        (PAD + 210, "空闲卡"),
        (PAD + 400, "占用卡"),
        (PAD + 580, "状态"),
    ]
    for cx, txt in cols:
        d.text((cx, y + 15), txt, fill=C_TEXT, font=font_b)
    y += HDR_H

    idle_all = 0
    busy_all = 0
    for i, r in enumerate(results):
        bg = C_ROW1 if i % 2 == 0 else C_ROW2
        d.rectangle([PAD, y, WIDTH - PAD, y + ROW_H], fill=bg, outline=C_BORDER)
        host = r['host']
        parts = host.split('.')
        disp_host = f"192.168.25.{parts[-1]}" if len(parts) >= 4 else host
        d.text((PAD + 15, y + 11), disp_host, fill=C_TEXT, font=font)

        if r.get('error'):
            d.text((PAD + 210, y + 11), "-", fill=C_TEXT, font=font)
            d.text((PAD + 400, y + 11), "-", fill=C_TEXT, font=font)
            d.text((PAD + 580, y + 11), "错误", fill=C_RED, font=font)
        else:
            ic = len(r.get('idle', []))
            bc = len(r.get('busy', []))
            idle_all += ic
            busy_all += bc

            d.text((PAD + 210, y + 11), f"{ic}卡", fill=C_GREEN if ic > 0 else C_GRAY, font=font)
            d.text((PAD + 252, y + 11), f"({fmt_list(r.get('idle', []))})", fill=C_GRAY, font=font)
            d.text((PAD + 400, y + 11), f"{bc}卡", fill=C_ORANGE if bc > 0 else C_GRAY, font=font)
            d.text((PAD + 442, y + 11), f"({fmt_list(r.get('busy', []))})", fill=C_GRAY, font=font)

            if ic > 0:
                status_text, status_color = "有空闲", C_GREEN
            elif bc > 0:
                status_text, status_color = "全占用", C_ORANGE
            else:
                status_text, status_color = "无卡", C_GRAY
            d.text((PAD + 580, y + 11), status_text, fill=status_color, font=font)
        y += ROW_H

    d.rectangle([PAD, y, WIDTH - PAD, y + FTR_H], fill=C_FTR, outline=C_BORDER, width=2)
    d.text((PAD + 15, y + 12), "总计", fill=C_TEXT, font=font_b)
    d.text((PAD + 210, y + 12), f"{idle_all} 卡空闲", fill=C_GREEN, font=font_b)
    d.text((PAD + 400, y + 12), f"{busy_all} 卡占用", fill=C_ORANGE, font=font_b)

    img.save(path, format='PNG')
    fsize = os.path.getsize(path)
    log.info(f"图片已生成: {path} ({fsize} bytes, PNG)")
    return path


def format_status_text(results):
    lines = []
    for r in results:
        if r.get('error'):
            lines.append(f"❌ {r['host']}: 错误 - {r['error']}")
        else:
            idle_str = ','.join(map(str, r['idle'])) if r['idle'] else '无'
            busy_str = ','.join(map(str, r['busy'])) if r['busy'] else '无'
            status = "有空闲" if r['idle'] else ("全占用" if r['busy'] else "无卡")
            lines.append(f"{'✅' if r['idle'] else '⚠️'} {r['host']}: "
                        f"总卡数={r['total']}, 空闲={len(r['idle'])}({idle_str}), "
                        f"占用={len(r['busy'])}({busy_str}), 状态={status}")

    total_idle = sum(len(r['idle']) for r in results if not r.get('error'))
    total_busy = sum(len(r['busy']) for r in results if not r.get('error'))
    lines.append(f"\n汇总: {total_idle} 卡空闲, {total_busy} 卡占用")
    return '\n'.join(lines)
