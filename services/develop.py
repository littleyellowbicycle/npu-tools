#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import threading
import concurrent.futures
import copy

from config import filter_servers, get_deploy_nodes, get_remote_dir, get_watch_dir, get_docker_config, build_docker_create_cmd
from services.npu_service import query_npu_status
from driver.ssh_driver import exec_command, upload_file

log = logging.getLogger("NPU-Tools.Service.Develop")

_watcher_thread = None
_watcher_stop_event = threading.Event()

LOG_DIR_NAME = "logs"


def _docker_exec_prefix(server):
    docker = get_docker_config(server)
    if not docker or not docker.get('container'):
        return None
    return f"docker exec {docker['container']}"


def _container_workdir(server):
    docker = get_docker_config(server)
    if docker and docker.get('workdir'):
        return docker['workdir']
    return get_remote_dir()


def _wrap_command(server, command):
    prefix = _docker_exec_prefix(server)
    if prefix:
        return f"{prefix} bash -c '{command}'"
    return command


def launch_script(script_path, hosts=None, args=None, remote_dir=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    if remote_dir is None:
        remote_dir = get_remote_dir()
    script_name = os.path.basename(script_path)
    remote_script_path = os.path.join(remote_dir, script_name).replace('\\', '/')
    log_dir = os.path.join(remote_dir, LOG_DIR_NAME).replace('\\', '/')
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    def _launch_on_server(server):
        host_ip = server['host'].replace('.', '-')
        log_file = os.path.join(log_dir, f"{os.path.splitext(script_name)[0]}_{host_ip}_{timestamp}.log").replace('\\', '/')

        docker = get_docker_config(server)
        if docker and docker.get('container'):
            if remote_dir != get_remote_dir():
                container_workdir = remote_dir
            else:
                container_workdir = docker.get('workdir', remote_dir)
            container_script = os.path.join(container_workdir, script_name).replace('\\', '/')
            container_log_dir = os.path.join(container_workdir, LOG_DIR_NAME).replace('\\', '/')
            container_log = os.path.join(container_log_dir, f"{os.path.splitext(script_name)[0]}_{host_ip}_{timestamp}.log").replace('\\', '/')

            mkdir_cmd = f"mkdir -p {log_dir} && mkdir -p {remote_dir}"
            r = exec_command(server, mkdir_cmd, 10)
            if not r['success']:
                r['script'] = script_name
                r['pid'] = None
                r['log_file'] = log_file
                r['success'] = False
                r['error'] = f"创建日志目录失败: {r.get('error', '')}"
                return r

            env_prefix = f"NODE_IP={server['host']} HOST_IP={server['host']}"
            inner_cmd = f"mkdir -p {container_log_dir} && {env_prefix} nohup python3 {container_script}"
            if args:
                inner_cmd += f' {args}'
            inner_cmd += f' > {container_log} 2>&1 & echo $!'

            command = f"docker exec {docker['container']} bash -c '{inner_cmd}'"
            r = exec_command(server, command, 15)
        else:
            env_prefix = f"NODE_IP={server['host']} HOST_IP={server['host']}"
            command = f'mkdir -p {log_dir} && {env_prefix} nohup python3 {remote_script_path}'
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


def sync_script(script_path, hosts=None, remote_dir=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    if not os.path.exists(script_path):
        return {'success': False, 'error': f'本地脚本不存在: {script_path}', 'results': []}

    if remote_dir is None:
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


def sync_directory(local_dir=None, hosts=None, remote_dir=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    if local_dir is None:
        local_dir = get_watch_dir()

    if not os.path.isdir(local_dir):
        return {'success': False, 'error': f'本地目录不存在: {local_dir}', 'results': []}

    if remote_dir is None:
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


def list_processes(hosts=None, keyword=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {}
        for s in target_servers:
            docker = get_docker_config(s)
            if docker and docker.get('container'):
                if keyword:
                    cmd = f"docker exec {docker['container']} ps aux | grep python3 | grep '{keyword}' | grep -v grep"
                else:
                    cmd = f"docker exec {docker['container']} ps aux | grep python3 | grep -v grep"
            else:
                if keyword:
                    cmd = f"ps aux | grep python3 | grep '{keyword}' | grep -v grep"
                else:
                    cmd = "ps aux | grep python3 | grep -v grep"
            futures[executor.submit(exec_command, s, cmd, 10)] = s

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

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {}
        for s in target_servers:
            docker = get_docker_config(s)
            if docker and docker.get('container'):
                cmd = f"docker exec {docker['container']} bash -c 'kill {pid} 2>/dev/null; sleep 1; kill -0 {pid} 2>/dev/null && echo \"still_running\" || echo \"stopped\"'"
            else:
                cmd = f'kill {pid} 2>/dev/null; sleep 1; kill -0 {pid} 2>/dev/null && echo "still_running" || echo "stopped"'
            futures[executor.submit(exec_command, s, cmd, 10)] = s

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


def setup_docker(hosts=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {}
        for s in target_servers:
            docker = get_docker_config(s)
            if not docker or not docker.get('container'):
                futures[executor.submit(_noop_no_docker, s)] = s
                continue

            create_cmd = build_docker_create_cmd(s)
            if not create_cmd:
                futures[executor.submit(_noop_no_docker, s)] = s
                continue

            check_cmd = f"docker ps -q -f name={docker['container']}"
            futures[executor.submit(_setup_single, s, docker, check_cmd, create_cmd)] = s

        for f in concurrent.futures.as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'success': False,
                    'error': str(e),
                    'container': '',
                    'action': ''
                })

    success_count = sum(1 for r in results if r['success'])
    return {
        'success': success_count > 0,
        'total': len(target_servers),
        'success_count': success_count,
        'fail_count': len(target_servers) - success_count,
        'results': results
    }


def _setup_single(server, docker, check_cmd, create_cmd):
    host = server['host']
    container = docker.get('container', '')

    r = exec_command(server, check_cmd, 10)
    if r['success'] and r.get('output', '').strip():
        return {
            'host': host,
            'container': container,
            'action': 'already_running',
            'success': True,
            'output': f'容器 {container} 已在运行'
        }

    r = exec_command(server, create_cmd, 30)
    container_id = r.get('output', '').strip()[:12] if r['success'] else ''
    return {
        'host': host,
        'container': container,
        'container_id': container_id,
        'action': 'created' if r['success'] else 'failed',
        'success': r['success'],
        'output': r.get('output', ''),
        'error': r.get('error', '')
    }


def _noop_no_docker(server):
    return {
        'host': server['host'],
        'container': '',
        'action': 'skipped',
        'success': True,
        'output': '未配置 Docker，跳过'
    }


def list_containers(hosts=None):
    target_servers = _resolve_servers(hosts)
    if not target_servers:
        return {'success': False, 'error': '没有匹配的目标服务器', 'results': []}

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(target_servers)) as executor:
        futures = {}
        for s in target_servers:
            docker = get_docker_config(s)
            if docker and docker.get('container'):
                cmd = f"docker ps -a --filter name={docker['container']} --format '{{{{.ID}}}} {{{{.Names}}}} {{{{.Status}}}} {{{{.Image}}}}'"
            else:
                cmd = "docker ps -a --format '{{.ID}} {{.Names}} {{.Status}} {{.Image}}' | head -10"
            futures[executor.submit(exec_command, s, cmd, 10)] = s

        for f in concurrent.futures.as_completed(futures):
            try:
                r = f.result()
                containers = []
                for line in r.get('output', '').strip().split('\n'):
                    if line.strip():
                        parts = line.strip().split(None, 3)
                        if len(parts) >= 4:
                            containers.append({
                                'id': parts[0],
                                'name': parts[1],
                                'status': parts[2],
                                'image': parts[3]
                            })
                r['containers'] = containers
                results.append(r)
            except Exception as e:
                results.append({
                    'host': futures[f]['host'],
                    'output': '',
                    'error': str(e),
                    'exit_code': -1,
                    'success': False,
                    'containers': []
                })

    return {
        'success': any(r['success'] for r in results),
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


def auto_deploy(script_path, count=4, args=None, min_idle_cards=1):
    results = query_npu_status()
    if not results:
        return {'success': False, 'error': '无法查询 NPU 状态', 'selected_hosts': [], 'results': []}

    candidates = []
    for r in results:
        if r.get('error'):
            continue
        idle_count = len(r.get('idle', []))
        if idle_count >= min_idle_cards:
            candidates.append({
                'host': r['host'],
                'idle_cards': idle_count,
                'idle_list': r['idle'],
                'total_cards': r['total'],
                'busy_cards': len(r.get('busy', []))
            })

    candidates.sort(key=lambda x: (-x['idle_cards'], x['host']))
    selected = candidates[:count]

    if not selected:
        return {
            'success': False,
            'error': f'没有满足条件的空闲节点（需要至少 {min_idle_cards} 张空闲卡）',
            'candidates': candidates,
            'selected_hosts': [],
            'results': []
        }

    selected_hosts = [s['host'] for s in selected]

    sync_result = sync_script(script_path, selected_hosts)
    if not sync_result['success']:
        return {
            'success': False,
            'error': '脚本同步失败',
            'selected_hosts': selected_hosts,
            'selection_detail': selected,
            'sync_result': sync_result,
            'results': []
        }

    launch_result = launch_script(script_path, selected_hosts, args)

    return {
        'success': launch_result['success'],
        'selected_hosts': selected_hosts,
        'selection_detail': selected,
        'requested_count': count,
        'actual_count': len(selected),
        'min_idle_cards': min_idle_cards,
        'sync_result': sync_result,
        'launch_result': launch_result,
        'results': launch_result.get('results', [])
    }


def smart_deploy(script_path, count=4, args=None, min_idle_cards=1, hosts=None, container=None, remote_dir=None):
    steps = []

    if hosts:
        selected_hosts = hosts if isinstance(hosts, list) else [hosts]
        selected = []
        for h in selected_hosts:
            selected.append({'host': h, 'idle_cards': '?', 'source': 'user_specified'})
        steps.append({
            'step': 'select_nodes',
            'success': True,
            'detail': selected,
            'message': f'用户指定 {len(selected_hosts)} 个节点: {", ".join(selected_hosts)}'
        })
    else:
        results = query_npu_status()
        if not results:
            return {'success': False, 'error': '无法查询 NPU 状态', 'steps': steps}

        candidates = []
        for r in results:
            if r.get('error'):
                continue
            idle_count = len(r.get('idle', []))
            if idle_count >= min_idle_cards:
                candidates.append({
                    'host': r['host'],
                    'idle_cards': idle_count,
                    'idle_list': r['idle'],
                    'total_cards': r['total'],
                    'busy_cards': len(r.get('busy', []))
                })

        candidates.sort(key=lambda x: (-x['idle_cards'], x['host']))
        selected = candidates[:count]

        if not selected:
            return {
                'success': False,
                'error': f'没有满足条件的空闲节点（需要至少 {min_idle_cards} 张空闲卡）',
                'candidates': candidates,
                'steps': steps
            }

        selected_hosts = [s['host'] for s in selected]
        steps.append({
            'step': 'select_nodes',
            'success': True,
            'detail': selected,
            'message': f'选中 {len(selected)} 个节点: {", ".join(selected_hosts)}'
        })

    if container:
        target_servers = filter_servers(selected_hosts)
        _container_overrides = {}
        for s in target_servers:
            docker = get_docker_config(s)
            if docker:
                overridden = copy.deepcopy(docker)
                overridden['container'] = container
                _container_overrides[s['host']] = overridden
                s['_docker_override'] = overridden

    docker_result = setup_docker(selected_hosts)
    docker_ok = docker_result.get('success', False)
    steps.append({
        'step': 'setup_docker',
        'success': docker_ok,
        'detail': docker_result.get('results', []),
        'message': f'Docker 设置: {docker_result.get("success_count", 0)}/{docker_result.get("total", 0)} 成功'
    })

    sync_result = sync_script(script_path, selected_hosts, remote_dir=remote_dir)
    if not sync_result['success']:
        steps.append({
            'step': 'sync_script',
            'success': False,
            'detail': sync_result,
            'message': f'脚本同步失败'
        })
        return {
            'success': False,
            'error': '脚本同步失败',
            'selected_hosts': selected_hosts,
            'steps': steps
        }
    steps.append({
        'step': 'sync_script',
        'success': True,
        'detail': sync_result,
        'message': f'脚本同步: {sync_result.get("success_count", 0)}/{sync_result.get("total", 0)} 成功'
    })

    launch_result = launch_script(script_path, selected_hosts, args, remote_dir=remote_dir)
    steps.append({
        'step': 'launch_script',
        'success': launch_result.get('success', False),
        'detail': launch_result.get('results', []),
        'message': f'脚本启动: {launch_result.get("success_count", 0)}/{launch_result.get("total", 0)} 成功'
    })

    return {
        'success': launch_result.get('success', False),
        'selected_hosts': selected_hosts,
        'selection_detail': selected,
        'requested_count': count,
        'actual_count': len(selected_hosts),
        'min_idle_cards': min_idle_cards,
        'steps': steps,
        'launch_result': launch_result,
        'results': launch_result.get('results', [])
    }


def _resolve_servers(hosts=None):
    if not hosts:
        deploy_nodes = get_deploy_nodes()
        if deploy_nodes:
            return filter_servers(deploy_nodes)
        return filter_servers()
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
