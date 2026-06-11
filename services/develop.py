#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import threading
import concurrent.futures

from config import filter_servers, get_deploy_nodes, get_remote_dir, get_watch_dir
from driver.ssh_driver import exec_command, upload_file

log = logging.getLogger("NPU-Tools.Service.Develop")

_watcher_thread = None
_watcher_stop_event = threading.Event()

LOG_DIR_NAME = "logs"


def launch_script(script_path, hosts=None, args=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    remote_dir = get_remote_dir()
    script_name = os.path.basename(script_path)
    remote_script_path = os.path.join(remote_dir, script_name).replace('\\', '/')
    log_dir = os.path.join(remote_dir, LOG_DIR_NAME).replace('\\', '/')
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    def _launch_on_server(server):
        host_ip = server['host'].replace('.', '-')
        log_file = os.path.join(log_dir, f"{os.path.splitext(script_name)[0]}_{host_ip}_{timestamp}.log").replace('\\', '/')
        command = f'mkdir -p {log_dir} && nohup python3 {remote_script_path}'
        if args:
            command += f' {args}'
        command += f' > {log_file} 2>&1 & echo $!'
        r = exec_command(server, command, 10)
        pid = r.get('output', '').strip().split('\n')[-1].strip()
        r['pid'] = pid if pid.isdigit() else None
        r['log_file'] = log_file
        r['script'] = script_name
        r['success'] = r['pid'] is not None
        if not r['success']:
            r['error'] = '未能获取进程 PID'
        return r

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {executor.submit(_launch_on_server, s): s for s in target_servers}
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'script': script_name,
                    'pid': None,
                    'log_file': '',
                    'output': '',
                    'error': str(e),
                    'exit_code': -1,
                    'success': False
                })

    success_count = sum(1 for r in results if r['success'])
    return {
        'success': success_count > 0,
        'total': len(target_servers),
        'success_count': success_count,
        'fail_count': len(target_servers) - success_count,
        'results': results
    }


def sync_script(script_path, hosts=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    if not os.path.exists(script_path):
        return {'success': False, 'error': f'本地脚本不存在: {script_path}', 'results': []}

    remote_dir = get_remote_dir()
    script_name = os.path.basename(script_path)
    remote_path = os.path.join(remote_dir, script_name).replace('\\', '/')

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {executor.submit(upload_file, s, script_path, remote_path): s for s in target_servers}
        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                server = futures[f]
                results.append({
                    'host': server['host'],
                    'local_path': script_path,
                    'remote_path': remote_path,
                    'success': False,
                    'error': str(e)
                })

    success_count = sum(1 for r in results if r['success'])
    return {
        'success': success_count > 0,
        'total': len(target_servers),
        'success_count': success_count,
        'fail_count': len(target_servers) - success_count,
        'results': results
    }


def sync_directory(local_dir=None, hosts=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    if local_dir is None:
        local_dir = get_watch_dir()

    if not os.path.isdir(local_dir):
        return {'success': False, 'error': f'本地目录不存在: {local_dir}', 'results': []}

    remote_dir = get_remote_dir()
    py_files = [f for f in os.listdir(local_dir) if f.endswith('.py')]

    all_results = []
    for script_name in py_files:
        local_path = os.path.join(local_dir, script_name)
        remote_path = os.path.join(remote_dir, script_name).replace('\\', '/')

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
            futures = {executor.submit(upload_file, s, local_path, remote_path): s for s in target_servers}
            for f in concurrent.futures.as_completed(futures):
                try:
                    result = f.result()
                    result['file'] = script_name
                    all_results.append(result)
                except Exception as e:
                    server = futures[f]
                    all_results.append({
                        'host': server['host'],
                        'file': script_name,
                        'success': False,
                        'error': str(e)
                    })

    success_count = sum(1 for r in all_results if r['success'])
    total_ops = len(py_files) * len(target_servers)
    return {
        'success': success_count == total_ops,
        'total_files': len(py_files),
        'total_servers': len(target_servers),
        'total_operations': total_ops,
        'success_count': success_count,
        'fail_count': total_ops - success_count,
        'results': all_results
    }


def start_watcher(hosts=None, interval=2):
    global _watcher_thread, _watcher_stop_event

    if _watcher_thread and _watcher_thread.is_alive():
        return {'success': False, 'error': '文件监控已在运行中'}

    watch_dir = get_watch_dir()
    if not os.path.isdir(watch_dir):
        return {'success': False, 'error': f'监控目录不存在: {watch_dir}'}

    _watcher_stop_event.clear()
    _watcher_thread = threading.Thread(
        target=_watch_loop,
        args=(watch_dir, hosts, interval),
        daemon=True
    )
    _watcher_thread.start()
    log.info(f"文件监控已启动: 目录={watch_dir}, 间隔={interval}s")
    return {'success': True, 'message': f'文件监控已启动: {watch_dir}'}


def stop_watcher():
    global _watcher_thread, _watcher_stop_event

    if not _watcher_thread or not _watcher_thread.is_alive():
        return {'success': False, 'error': '文件监控未在运行'}

    _watcher_stop_event.set()
    _watcher_thread.join(timeout=5)
    log.info("文件监控已停止")
    return {'success': True, 'message': '文件监控已停止'}


def get_script_log(log_file, hosts=None, lines=50):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    command = f'tail -n {lines} {log_file}'

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {executor.submit(exec_command, s, command, 10): s for s in target_servers}
        for f in concurrent.futures.as_completed(futures):
            try:
                r = f.result()
                r['log_file'] = log_file
                results.append(r)
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'log_file': log_file,
                    'output': '',
                    'error': str(e),
                    'exit_code': -1,
                    'success': False
                })

    return {
        'success': any(r['success'] for r in results),
        'log_file': log_file,
        'lines': lines,
        'results': results
    }


def list_logs(hosts=None, keyword=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    remote_dir = get_remote_dir()
    log_dir = os.path.join(remote_dir, LOG_DIR_NAME).replace('\\', '/')

    if keyword:
        command = f'ls -lt {log_dir}/*{keyword}*.log 2>/dev/null | head -20'
    else:
        command = f'ls -lt {log_dir}/*.log 2>/dev/null | head -20'

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {executor.submit(exec_command, s, command, 10): s for s in target_servers}
        for f in concurrent.futures.as_completed(futures):
            try:
                r = f.result()
                r['log_files'] = _parse_ls_output(r.get('output', ''))
                results.append(r)
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'output': '',
                    'error': str(e),
                    'exit_code': -1,
                    'success': False,
                    'log_files': []
                })

    return {
        'success': any(r['success'] for r in results),
        'keyword': keyword,
        'results': results
    }


def _parse_ls_output(output):
    log_files = []
    for line in output.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split(None, 8)
        if len(parts) >= 9:
            log_files.append({
                'permissions': parts[0],
                'size': parts[4],
                'date': f"{parts[5]} {parts[6]} {parts[7]}",
                'path': parts[8]
            })
    return log_files


def list_processes(hosts=None, keyword=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    if keyword:
        command = f"ps aux | grep python3 | grep '{keyword}' | grep -v grep"
    else:
        command = "ps aux | grep python3 | grep -v grep"

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {executor.submit(exec_command, s, command, 10): s for s in target_servers}
        for f in concurrent.futures.as_completed(futures):
            try:
                r = f.result()
                r['processes'] = _parse_ps_output(r.get('output', ''))
                results.append(r)
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'output': '',
                    'error': str(e),
                    'exit_code': -1,
                    'success': False,
                    'processes': []
                })

    return {
        'success': any(r['success'] for r in results),
        'keyword': keyword,
        'results': results
    }


def stop_script(pid, hosts=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    command = f'kill {pid} 2>/dev/null; sleep 1; kill -0 {pid} 2>/dev/null && echo "still_running" || echo "stopped"'

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {executor.submit(exec_command, s, command, 10): s for s in target_servers}
        for f in concurrent.futures.as_completed(futures):
            try:
                r = f.result()
                output = r.get('output', '').strip()
                r['pid'] = pid
                r['stopped'] = 'stopped' in output
                results.append(r)
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'pid': pid,
                    'output': '',
                    'error': str(e),
                    'exit_code': -1,
                    'success': False,
                    'stopped': False
                })

    success_count = sum(1 for r in results if r.get('stopped'))
    return {
        'success': success_count > 0,
        'total': len(target_servers),
        'success_count': success_count,
        'fail_count': len(target_servers) - success_count,
        'results': results
    }


def _parse_ps_output(output):
    processes = []
    for line in output.strip().split('\n'):
        if not line.strip():
            continue
        parts = line.split(None, 10)
        if len(parts) >= 11:
            processes.append({
                'user': parts[0],
                'pid': parts[1],
                'cpu': parts[2],
                'mem': parts[3],
                'vsz': parts[4],
                'rss': parts[5],
                'start': parts[8],
                'time': parts[9],
                'command': parts[10]
            })
    return processes


def _resolve_servers(hosts=None):
    if not hosts:
        deploy_nodes = get_deploy_nodes()
        if deploy_nodes:
            return filter_servers(deploy_nodes)
    return filter_servers(hosts)


def _watch_loop(watch_dir, hosts, interval):
    file_mtimes = {}
    py_files = [f for f in os.listdir(watch_dir) if f.endswith('.py')]
    for f in py_files:
        full_path = os.path.join(watch_dir, f)
        try:
            file_mtimes[f] = os.path.getmtime(full_path)
        except OSError:
            pass

    while not _watcher_stop_event.is_set():
        try:
            current_files = [f for f in os.listdir(watch_dir) if f.endswith('.py')]
            changed_files = []

            for f in current_files:
                full_path = os.path.join(watch_dir, f)
                try:
                    mtime = os.path.getmtime(full_path)
                    if f not in file_mtimes or file_mtimes[f] != mtime:
                        changed_files.append(f)
                        file_mtimes[f] = mtime
                except OSError:
                    pass

            for f in list(file_mtimes.keys()):
                if f not in current_files:
                    del file_mtimes[f]

            if changed_files:
                log.info(f"检测到文件变更: {changed_files}")
                for f in changed_files:
                    local_path = os.path.join(watch_dir, f)
                    result = sync_script(local_path, hosts)
                    status = "成功" if result['success'] else "部分失败"
                    log.info(f"同步 {f}: {status} ({result['success_count']}/{result['total']})")

        except Exception as e:
            log.error(f"监控循环异常: {e}")

        _watcher_stop_event.wait(interval)
