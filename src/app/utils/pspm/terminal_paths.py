"""终端路径、提示符和 Tab 补全基础工具。

本模块集中维护不依赖数据库和 WebSocket 状态的终端纯工具函数。
接口层只负责调用这些工具，不再直接承载路径解析、提示符格式化和本地补全细节。
"""

from __future__ import annotations

import asyncio
import os
import shlex
from typing import List, Tuple

from fastapi import HTTPException

from app.utils.pspm.conda_utils import detect_conda_init_on_local
from app.utils.pspm.project_config import TERMINAL_HOME_DIR
from app.utils.pspm.terminal_config import terminal_message

# 终端默认根目录。当前系统以 root 管理远端服务器，因此 shell 展示以该路径为 home。
HOME_DIR = TERMINAL_HOME_DIR


def _format_prompt(host_label: str, cwd: str) -> str:
    """生成终端提示符文本。

    参数：
    - host_label：终端标签中展示的主机别名。
    - cwd：当前会话所在工作目录。

    返回：
    - str：形如 `(base) [root@host ~]#` 的提示符。
    """
    if cwd == HOME_DIR:
        display_path = '~'
    elif cwd.startswith(f'{HOME_DIR}/'):
        display_path = f'~{cwd[len(HOME_DIR):]}'
    else:
        display_path = cwd
    return f'(base) [root@{host_label} {display_path}]#'



def _format_prompt_with_env(host_label: str, cwd: str, conda_env: str | None = None) -> str:
    """根据会话保存的 Conda 环境生成提示符。"""
    env_name = (conda_env or '').strip() or 'base'
    if cwd == HOME_DIR:
        display_path = '~'
    elif cwd.startswith(f'{HOME_DIR}/'):
        display_path = f'~{cwd[len(HOME_DIR):]}'
    else:
        display_path = cwd
    return f'({env_name}) [root@{host_label} {display_path}]#'


def _normalize_cwd(cwd: str | None) -> str:
    """规范化终端会话工作目录。

    参数：
    - cwd：会话中保存的目录，可能为空。

    返回：
    - str：绝对路径形式的工作目录。
    """
    path = (cwd or '').strip() or HOME_DIR
    path = os.path.normpath(path)
    if not path.startswith('/'):
        path = f'/{path}'
    return path


def _resolve_path(current_cwd: str, target: str | None) -> str:
    """把 cd 命令目标转换为绝对路径。

    参数：
    - current_cwd：当前会话目录。
    - target：用户输入的目标路径。

    返回：
    - str：解析后的绝对路径。
    """
    raw = (target or '').strip()
    if not raw or raw == '~':
        return HOME_DIR

    if raw.startswith('~/'):
        candidate = f"{HOME_DIR}/{raw[2:]}"
    elif raw.startswith('/'):
        candidate = raw
    else:
        candidate = f"{current_cwd.rstrip('/')}/{raw}"

    normalized = os.path.normpath(candidate)
    if not normalized.startswith('/'):
        normalized = f'/{normalized}'
    return normalized


def _split_command(command: str) -> List[str]:
    """安全拆分终端命令参数。

    参数：
    - command：用户在终端输入框提交的命令。

    返回：
    - List[str]：按 shell 规则拆分后的参数列表。
    """
    try:
        return shlex.split(command)
    except ValueError:
        raise HTTPException(status_code=400, detail=terminal_message('bad_command_format'))


def _extract_path_token(command: str) -> Tuple[str, str]:
    """提取命令中正在补全的最后一个路径片段。

    参数：
    - command：用户当前输入的完整命令。

    返回：
    - Tuple[str, str]：最后片段前缀和待补全 token。
    """
    raw = str(command or '')
    if not raw.strip():
        return raw, ''
    if raw.endswith(' '):
        return raw, ''
    token = raw.split()[-1]
    prefix = raw[:-len(token)] if token else raw
    return prefix, token


def _resolve_completion_base(cwd: str, token: str) -> Tuple[str, str]:
    """计算路径自动补全需要扫描的目录和文件名前缀。

    参数：
    - cwd：当前会话目录。
    - token：待补全路径片段。

    返回：
    - Tuple[str, str]：基础目录和文件名前缀。
    """
    if token.startswith('~/'):
        abs_target = f"{HOME_DIR}/{token[2:]}"
        display_prefix = '~/'
    elif token.startswith('/'):
        abs_target = token
        display_prefix = '/'
    elif token.startswith('~'):
        abs_target = HOME_DIR
        display_prefix = '~'
    else:
        abs_target = os.path.join(cwd, token)
        display_prefix = ''

    normalized = os.path.normpath(abs_target)
    if token.endswith('/') and not normalized.endswith('/'):
        normalized = f'{normalized}/'

    base_dir = normalized if normalized.endswith('/') else os.path.dirname(normalized)
    base_name = '' if normalized.endswith('/') else os.path.basename(normalized)

    if not base_dir:
        base_dir = '/'
    return os.path.normpath(base_dir), base_name


def _to_completion_display(cwd: str, token: str, abs_path: str, is_dir: bool) -> str:
    """把补全候选转换为接近真实 shell 的候选展示值。

    参数：
    - cwd：当前终端工作目录。
    - token：用户正在补全的路径片段。
    - abs_path：候选项的绝对路径。
    - is_dir：候选项是否为目录。

    返回：
    - str：相对当前 token 的补全展示值，目录会以 `/` 结尾。
    """
    normalized = os.path.normpath(abs_path)
    if token.startswith('/'):
        display = normalized
    elif token.startswith('~/'):
        home_path = f'{HOME_DIR}/'
        if normalized.startswith(home_path):
            display = f'~/{normalized[len(home_path):]}'
        else:
            display = normalized
    else:
        base_dir, _base_name = _resolve_completion_base(cwd, token)
        name = os.path.basename(normalized.rstrip('/'))
        token_dir = ''
        if '/' in token:
            token_dir = token.rsplit('/', 1)[0].rstrip('/')
        display = f'{token_dir}/{name}' if token_dir else name
    if is_dir and not display.endswith('/'):
        display = f'{display}/'
    return display

def _to_display_path(abs_path: str) -> str:
    """把绝对路径转换为终端中更友好的展示路径。

    参数：
    - abs_path：候选文件或目录的绝对路径。

    返回：
    - str：以 `~` 简写后的展示路径。
    """
    normalized = os.path.normpath(abs_path)
    if normalized == HOME_DIR:
        return '~'
    if normalized.startswith(f'{HOME_DIR}/'):
        return f"~/{normalized[len(HOME_DIR)+1:]}"
    return normalized


def _complete_path_candidates(cwd: str, token: str) -> List[str]:
    """列出路径自动补全候选项。

    参数：
    - cwd：当前会话目录。
    - token：待补全路径片段。

    返回：
    - List[str]：可选路径候选列表。
    """
    base_dir, base_name = _resolve_completion_base(cwd, token)
    if not os.path.isdir(base_dir):
        return []

    candidates: List[str] = []
    try:
        for entry in os.scandir(base_dir):
            name = entry.name
            if not name.startswith(base_name):
                continue
            abs_candidate = os.path.join(base_dir, name)
            display = _to_display_path(abs_candidate)
            if entry.is_dir():
                display = f'{display}/'
            candidates.append(display)
    except Exception:
        return []
    return sorted(set(candidates))


def _common_prefix(values: List[str]) -> str:
    """计算多个补全候选项的公共前缀。

    参数：
    - values：候选字符串列表。

    返回：
    - str：所有候选项共有的最长前缀。
    """
    if not values:
        return ''
    prefix = values[0]
    for value in values[1:]:
        i = 0
        limit = min(len(prefix), len(value))
        while i < limit and prefix[i] == value[i]:
            i += 1
        prefix = prefix[:i]
        if not prefix:
            break
    return prefix


def _is_command_token(prefix: str, token: str) -> bool:
    """判断当前 token 是否应该按命令名补全。

    参数：
    - prefix：token 前面的命令内容。
    - token：待补全片段。

    返回：
    - bool：True 表示补全命令名，False 表示补全路径。
    """
    if not token:
        return False
    if prefix.strip():
        return False
    if token.startswith(('~', '.', '/')):
        return False
    if '/' in token:
        return False
    return True


async def _complete_command_candidates(token: str) -> List[str]:
    """通过 bash compgen 查询命令名补全候选项。

    参数：
    - token：待补全命令名前缀。

    返回：
    - List[str]：命令名候选列表。
    """
    if not token:
        return []

    try:
        conda_init = await detect_conda_init_on_local()
    except HTTPException:
        conda_init = 'true'
    cmd = f"{conda_init}; compgen -c -- {shlex.quote(token)} | sort -u"
    process = await asyncio.create_subprocess_exec(
        '/bin/bash',
        '-lc',
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    timed_out = False
    try:
        stdout_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, _ = await process.communicate()

    if timed_out:
        return []

    lines = (stdout_bytes or b'').decode('utf-8', errors='replace').splitlines()
    values = []
    for line in lines:
        item = (line or '').strip()
        if not item:
            continue
        if not item.startswith(token):
            continue
        values.append(item)

    return sorted(set(values))
