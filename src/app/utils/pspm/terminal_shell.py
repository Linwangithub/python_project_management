"""终端远程 shell、SSH 和补全执行工具。

本模块封装所有与目标服务器命令执行有关的细节，包括 SSH 命令构造、远端 bash 包装、
命令执行、目录判断和 Tab 补全查询。接口层只关心“执行什么”，不用关心如何连接。
"""

from __future__ import annotations

import os
import re
import shlex
from typing import List

from fastapi import WebSocketException, status

from app import models
from app.utils.pspm.project_config import (
    TERMINAL_ASKPASS_TEMPLATE,
    TERMINAL_FOREGROUND_LOG_TEMPLATE,
    TERMINAL_HOME_DIR,
)
from app.utils.pspm.conda_utils import (
    build_conda_context_shell_command,
    context_needs_user_switch,
    detect_conda_context_on_server,
    run_shell_in_conda_context_on_server,
)
from app.utils.pspm.shell_utils import _is_local_server_ip_async, _run_server_shell, _run_shell
from app.utils.pspm.terminal_config import (
    SHELL_MARK_LOG,
    SHELL_MARK_LOG_BEGIN,
    SHELL_MARK_LOG_END,
    SHELL_MARK_PID,
    TERMINAL_DEFAULT_CONDA_ENV,
)
from app.utils.pspm.terminal_paths import _normalize_cwd, _resolve_completion_base, _to_completion_display


def _wrap_remote_bash(script: str) -> str:
    """把脚本包装成可交给目标服务器执行的 bash -lc 命令。"""
    return f'bash -lc {shlex.quote(script)}'

async def _remote_path_is_dir(server_row, path: str) -> bool:
    """在终端会话对应服务器上判断目录是否存在。"""
    safe_path = shlex.quote(_normalize_cwd(path))
    code, _out, _err = await run_shell_in_conda_context_on_server(
        server_row,
        f'test -d {safe_path}',
        timeout=10,
        include_conda_init=False,
    )
    return code == 0

async def _run_terminal_command_on_server(
    server_row,
    command: str,
    cwd: str,
    timeout: int,
    conda_env_name: str = TERMINAL_DEFAULT_CONDA_ENV,
    detach: bool = False,
) -> tuple[int, str, str]:
    """在终端会话对应服务器的指定目录和 Conda 环境中执行命令。"""
    safe_cwd = shlex.quote(_normalize_cwd(cwd))
    env_name = (conda_env_name or TERMINAL_DEFAULT_CONDA_ENV).strip() or TERMINAL_DEFAULT_CONDA_ENV
    conda_context = await detect_conda_context_on_server(server_row)
    activate = '' if env_name == 'base' else f'conda activate {shlex.quote(env_name)} >/dev/null 2>&1 && '
    if detach:
        script = (
            f'cd {safe_cwd} && {conda_context.init_command}; {activate}'
            f'log_file=$(mktemp {TERMINAL_FOREGROUND_LOG_TEMPLATE}); '
            f': > "$log_file"; '
            f'({command}) >> "$log_file" 2>&1 & '
            f'pid=$!; '
            f'echo "{SHELL_MARK_PID}=$pid"; '
            f'echo "{SHELL_MARK_LOG}=$log_file"; '
            f'sleep 1; '
            f'echo "{SHELL_MARK_LOG_BEGIN}"; tail -n 80 "$log_file" 2>/dev/null || true; echo "{SHELL_MARK_LOG_END}"'
        )
    else:
        script = f'cd {safe_cwd} && {conda_context.init_command}; {activate}{command}'
    return await _run_server_shell(server_row, build_conda_context_shell_command(conda_context, script), timeout=timeout)

async def _complete_command_candidates_on_server(server_row, token: str) -> List[str]:
    """在终端会话对应服务器上查询命令名补全候选项。"""
    if not token:
        return []

    script = f'compgen -c -- {shlex.quote(token)} | sort -u'
    code, out, _err = await run_shell_in_conda_context_on_server(server_row, script, timeout=10, include_conda_init=True)
    if code != 0:
        return []

    values = []
    for line in (out or '').splitlines():
        item = (line or '').strip()
        if item and item.startswith(token):
            values.append(item)
    return sorted(set(values))

async def _complete_path_candidates_on_server(server_row, cwd: str, token: str) -> List[str]:
    """在目标服务器上查询路径补全候选项。

    参数：
    - server_row：目标服务器配置记录。
    - cwd：当前终端工作目录。
    - token：用户正在补全的路径片段。

    返回：
    - List[str]：接近真实 shell 行为的路径补全候选项。
    """
    base_dir, base_name = _resolve_completion_base(cwd, token)
    script = (
        f'base={shlex.quote(base_dir)}; '
        f'prefix={shlex.quote(base_name)}; '
        'if [ -d "$base" ]; then '
        'for item in "$base"/"$prefix"*; do '
        '  [ -e "$item" ] || continue; '
        '  [ -d "$item" ] && printf "d\t%s\n" "$item" || printf "f\t%s\n" "$item"; '
        'done | head -n 200; '
        'fi'
    )
    code, out, _err = await run_shell_in_conda_context_on_server(
        server_row,
        script,
        timeout=3,
        include_conda_init=False,
    )
    if code != 0:
        return []

    candidates: List[str] = []
    for line in (out or '').splitlines():
        raw = (line or '').strip()
        if not raw or '\t' not in raw:
            continue
        kind, abs_path = raw.split('\t', 1)
        name = os.path.basename(abs_path.rstrip('/'))
        if not name.startswith(base_name):
            continue
        candidates.append(_to_completion_display(cwd, token, abs_path, kind == 'd'))
    return sorted(set(candidates))

def _build_sshpass_prefix(password: str) -> str:
    """为单次 SSH 进程构造 sshpass 密码前缀。"""
    if not password:
        return ''
    safe_password = shlex.quote(str(password))
    return f"sshpass -p {safe_password} "

def _build_askpass_ssh_command(password: str, ssh_command: str) -> str:
    """在缺少 sshpass 时构造基于 SSH_ASKPASS 的 SSH 命令。"""
    askpass_body = f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(str(password or ''))}\n"
    askpass_body_quoted = shlex.quote(askpass_body)
    ssh_command_quoted = shlex.quote(ssh_command)
    return (
        f'askpass_script=$(mktemp {TERMINAL_ASKPASS_TEMPLATE}) || exit 90; '
        'trap \'rm -f "$askpass_script"\' EXIT; '
        f'printf %s {askpass_body_quoted} > "$askpass_script"; '
        'chmod 700 "$askpass_script"; '
        f'DISPLAY=pspm:0 SSH_ASKPASS="$askpass_script" SSH_ASKPASS_REQUIRE=force setsid bash -lc {ssh_command_quoted}'
    )

def _build_conda_user_interactive_shell_command(conda_context) -> str:
    """构造登录到 Conda 所属用户的交互式 shell 命令。"""
    rc_line = conda_context.init_command
    script = (
        'tmp_rc=$(mktemp /tmp/pspm_conda_rc_XXXXXX) || exit 90; '
        'trap \'rm -f "$tmp_rc"\' EXIT; '
        f'printf "%s\\n" {shlex.quote(rc_line)} > "$tmp_rc"; '
        'bash --rcfile "$tmp_rc" -i'
    )
    return f'su - {shlex.quote(conda_context.execution_user)} -s /bin/bash -c {shlex.quote(script)}'


async def _get_terminal_default_cwd(server_row: models.pspm.PspmServer) -> str:
    """读取终端会话初始目录；普通用户 Conda 使用对应用户 home。"""
    try:
        conda_context = await detect_conda_context_on_server(server_row)
    except Exception:
        return TERMINAL_HOME_DIR
    if context_needs_user_switch(conda_context):
        return conda_context.home_dir or TERMINAL_HOME_DIR
    return TERMINAL_HOME_DIR


async def _build_terminal_process_command(server_row: models.pspm.PspmServer) -> List[str]:
    """构造本地 PTY 需要启动的本地 shell 或交互式 SSH shell 命令。"""
    ip = str(getattr(server_row, 'ip', '') or '').strip()
    if await _is_local_server_ip_async(ip):
        return ['/bin/bash', '-l']

    if not re.match(r'^[A-Za-z0-9_.:-]+$', ip):
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason=f'服务器IP格式不合法：{ip}')

    remote_shell = ''
    try:
        conda_context = await detect_conda_context_on_server(server_row)
        if context_needs_user_switch(conda_context):
            remote_shell = _build_conda_user_interactive_shell_command(conda_context)
    except Exception:
        remote_shell = ''

    ssh_port = int(getattr(server_row, 'ssh_port', 22) or 22)
    password = str(getattr(server_row, 'root_password', '') or '')
    remote_target = f"root@{shlex.quote(ip)}"
    remote_command = f" {shlex.quote(remote_shell)}" if remote_shell else ""
    base_ssh = (
        f"ssh -tt -p {ssh_port} "
        "-o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null "
        "-o ConnectTimeout=8 "
        "-o LogLevel=ERROR "
        "-o ServerAliveInterval=15 "
        "-o ServerAliveCountMax=2 "
        f"{remote_target}{remote_command}"
    )

    has_sshpass = (await _run_shell('command -v sshpass >/dev/null 2>&1', timeout=5))[0] == 0
    if password and has_sshpass:
        return ['/bin/bash', '-lc', f'{_build_sshpass_prefix(password)}{base_ssh}']

    has_setsid = (await _run_shell('command -v setsid >/dev/null 2>&1', timeout=5))[0] == 0
    if password and has_setsid:
        return ['/bin/bash', '-lc', _build_askpass_ssh_command(password, base_ssh)]

    if password:
        raise WebSocketException(code=status.WS_1011_INTERNAL_ERROR, reason='当前后端缺少 sshpass/setsid，无法创建密码 SSH 终端')
    return ['/bin/bash', '-lc', base_ssh]
