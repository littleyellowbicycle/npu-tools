#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import paramiko
import logging

log = logging.getLogger("NPU-Tools.Driver.SSH")


def exec_command(server, command, timeout=10):
    client = None
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
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        output = stdout.read().decode('utf-8', errors='replace').strip()
        error = stderr.read().decode('utf-8', errors='replace').strip()
        exit_code = stdout.channel.recv_exit_status()
        return {
            'host': server['host'],
            'output': output,
            'error': error,
            'exit_code': exit_code,
            'success': exit_code == 0
        }
    except Exception as e:
        return {
            'host': server['host'],
            'output': '',
            'error': str(e),
            'exit_code': -1,
            'success': False
        }
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def upload_file(server, local_path, remote_path):
    client = None
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
        sftp = client.open_sftp()
        remote_dir = os.path.dirname(remote_path)
        try:
            sftp.stat(remote_dir)
        except FileNotFoundError:
            _mkdir_p(sftp, remote_dir)
        sftp.put(local_path, remote_path)
        sftp.close()
        return {
            'host': server['host'],
            'local_path': local_path,
            'remote_path': remote_path,
            'success': True,
            'error': None
        }
    except Exception as e:
        return {
            'host': server['host'],
            'local_path': local_path,
            'remote_path': remote_path,
            'success': False,
            'error': str(e)
        }
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass


def _mkdir_p(sftp, remote_directory):
    dirs_to_create = []
    current = remote_directory
    while True:
        try:
            sftp.stat(current)
            break
        except FileNotFoundError:
            dirs_to_create.append(current)
            current = os.path.dirname(current)
            if not current:
                break
    for d in reversed(dirs_to_create):
        try:
            sftp.mkdir(d)
        except IOError:
            pass
