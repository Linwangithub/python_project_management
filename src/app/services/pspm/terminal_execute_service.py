"""终端命令执行服务模块。

用途：
- 承接旧 HTTP 终端会话中的命令执行、cd、conda activate/deactivate 等业务逻辑。
- API 层只负责权限依赖和响应模型声明。
"""

from __future__ import annotations

import shlex

from fastapi import HTTPException

from app import schemas
from app.services.pspm.terminal_access import _get_session_server_row
from app.services.pspm.terminal_legacy_session import (
    _get_session_data,
    _update_session_cwd,
    terminal_lock,
    terminal_sessions,
)
from app.utils.pspm.project_config import TERMINAL_COMMAND_TIMEOUT_SECONDS, TERMINAL_DEFAULT_HOST_LABEL, TERMINAL_HOME_DIR
from app.utils.pspm.terminal_config import TERMINAL_DEFAULT_CONDA_ENV, terminal_message
from app.utils.pspm.terminal_paths import _format_prompt_with_env, _normalize_cwd, _resolve_path, _split_command
from app.utils.pspm.terminal_shell import _remote_path_is_dir, _run_terminal_command_on_server

COMMAND_TIMEOUT_SECONDS = TERMINAL_COMMAND_TIMEOUT_SECONDS
DEFAULT_HOST_LABEL = TERMINAL_DEFAULT_HOST_LABEL
HOME_DIR = TERMINAL_HOME_DIR


def _terminal_execute_result(
    *,
    session_id: str,
    command: str,
    cwd: str,
    prompt_before: str,
    prompt_after: str,
    exit_code: int,
    stdout: str = '',
    stderr: str = '',
    blocked: bool = False,
    message: str = '',
) -> schemas.pspm.TerminalExecuteResult:
    """创建终端命令执行结果模型。

    参数：
    - session_id：终端会话 ID。
    - command：用户输入命令。
    - cwd：命令执行时工作目录。
    - prompt_before：执行前提示符。
    - prompt_after：执行后提示符。
    - exit_code：命令退出码。
    - stdout：标准输出。
    - stderr：标准错误。
    - blocked：是否被系统策略拦截。
    - message：用户可读结果消息。

    返回：
    - TerminalExecuteResult：接口响应 data。
    """
    return schemas.pspm.TerminalExecuteResult(
        session_id=session_id,
        command=command,
        cwd=cwd,
        prompt_before=prompt_before,
        prompt_after=prompt_after,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        blocked=blocked,
        message=message or terminal_message('ok'),
    )


def _terminal_execute_error_response(
    *,
    status_code: int,
    message: str,
    result: schemas.pspm.TerminalExecuteResult,
) -> schemas.pspm.TerminalExecuteResponse:
    """创建终端命令执行失败响应。"""
    return schemas.pspm.TerminalExecuteResponse(status='error', code=status_code, message=message, data=result)


def _is_simple_shell_builtin_command(command: str) -> bool:
    """判断命令是否不包含 shell 组合操作符，便于安全处理 cd/conda。"""
    return all(op not in command for op in ['&&', '||', ';', '|'])


async def execute_terminal_command_service(session, current_user, payload: schemas.pspm.TerminalExecuteRequest) -> schemas.pspm.TerminalExecuteResponse:
    """执行终端命令并返回标准化响应。

    参数：
    - session：数据库会话，用于按会话服务器 ID 查询服务器信息。
    - current_user：当前登录用户。
    - payload：终端会话 ID、命令文本和可选执行模式。

    返回：
    - TerminalExecuteResponse：命令执行结果。
    """
    command = (payload.command or '').strip()
    if not command:
        raise HTTPException(status_code=400, detail=terminal_message('command_required'))

    session_data = await _get_session_data(payload.session_id, current_user.id)
    server_row = await _get_session_server_row(session, current_user, session_data)
    cwd = _normalize_cwd(session_data.get('cwd'))
    host_label = (session_data.get('host_label') or DEFAULT_HOST_LABEL).strip() or DEFAULT_HOST_LABEL
    active_env = str(session_data.get('conda_env_name') or TERMINAL_DEFAULT_CONDA_ENV)
    prompt_before = _format_prompt_with_env(host_label, cwd, active_env)

    tokens = _split_command(command)
    if not tokens:
        raise HTTPException(status_code=400, detail=terminal_message('command_required'))

    primary = tokens[0].lower()

    if primary == 'python' and len(tokens) == 1:
        msg = terminal_message('interactive_python_unsupported')
        result = _terminal_execute_result(
            session_id=payload.session_id,
            command=command,
            cwd=cwd,
            prompt_before=prompt_before,
            prompt_after=prompt_before,
            exit_code=2,
            stderr=msg,
            blocked=True,
            message=msg,
        )
        return _terminal_execute_error_response(status_code=400, message=msg, result=result)

    if primary == 'cd' and _is_simple_shell_builtin_command(command):
        target = tokens[1] if len(tokens) > 1 else HOME_DIR
        next_cwd = _resolve_path(cwd, target)
        if not await _remote_path_is_dir(server_row, next_cwd):
            msg = terminal_message('bash_cd_missing', target=target)
            result = _terminal_execute_result(
                session_id=payload.session_id,
                command=command,
                cwd=cwd,
                prompt_before=prompt_before,
                prompt_after=prompt_before,
                exit_code=1,
                stderr=msg,
                message=terminal_message('directory_missing'),
            )
            return _terminal_execute_error_response(status_code=400, message=terminal_message('directory_missing'), result=result)

        updated = await _update_session_cwd(payload.session_id, current_user.id, next_cwd)
        next_prompt = _format_prompt_with_env(updated.get('host_label') or DEFAULT_HOST_LABEL, next_cwd, updated.get('conda_env_name'))
        return schemas.pspm.TerminalExecuteResponse(data=_terminal_execute_result(
            session_id=payload.session_id,
            command=command,
            cwd=next_cwd,
            prompt_before=prompt_before,
            prompt_after=next_prompt,
            exit_code=0,
        ))

    if primary == 'conda' and len(tokens) >= 3 and tokens[1].lower() == 'activate' and _is_simple_shell_builtin_command(command):
        env_name = tokens[2].strip()
        check_code, check_out, check_err = await _run_terminal_command_on_server(
            server_row,
            f"conda env list | awk '{{print $1}}' | grep -Fx {shlex.quote(env_name)} >/dev/null",
            cwd,
            COMMAND_TIMEOUT_SECONDS,
            TERMINAL_DEFAULT_CONDA_ENV,
            False,
        )
        if check_code != 0:
            msg = check_err.strip() or check_out.strip() or terminal_message('conda_env_missing', env_name=env_name)
            result = _terminal_execute_result(
                session_id=payload.session_id,
                command=command,
                cwd=cwd,
                prompt_before=prompt_before,
                prompt_after=prompt_before,
                exit_code=1,
                stderr=msg,
                message=msg,
            )
            return _terminal_execute_error_response(status_code=400, message=msg, result=result)
        async with terminal_lock:
            data = terminal_sessions.get(payload.session_id)
            if not data or data.get('user_id') != current_user.id:
                raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
            data['conda_env_name'] = env_name
        prompt_after = _format_prompt_with_env(host_label, cwd, env_name)
        return schemas.pspm.TerminalExecuteResponse(data=_terminal_execute_result(
            session_id=payload.session_id,
            command=command,
            cwd=cwd,
            prompt_before=prompt_before,
            prompt_after=prompt_after,
            exit_code=0,
        ))

    if primary == 'conda' and len(tokens) >= 2 and tokens[1].lower() == 'deactivate' and _is_simple_shell_builtin_command(command):
        async with terminal_lock:
            data = terminal_sessions.get(payload.session_id)
            if not data or data.get('user_id') != current_user.id:
                raise HTTPException(status_code=404, detail=terminal_message('session_not_found'))
            data['conda_env_name'] = TERMINAL_DEFAULT_CONDA_ENV
        prompt_after = _format_prompt_with_env(host_label, cwd, TERMINAL_DEFAULT_CONDA_ENV)
        return schemas.pspm.TerminalExecuteResponse(data=_terminal_execute_result(
            session_id=payload.session_id,
            command=command,
            cwd=cwd,
            prompt_before=prompt_before,
            prompt_after=prompt_after,
            exit_code=0,
        ))

    detach = str(getattr(payload, 'mode', '') or '').strip().lower() == 'foreground_start'
    exit_code, stdout, stderr = await _run_terminal_command_on_server(
        server_row,
        command,
        cwd,
        COMMAND_TIMEOUT_SECONDS,
        active_env,
        detach,
    )

    prompt_after = _format_prompt_with_env(host_label, cwd, active_env)
    return schemas.pspm.TerminalExecuteResponse(data=_terminal_execute_result(
        session_id=payload.session_id,
        command=command,
        cwd=cwd,
        prompt_before=prompt_before,
        prompt_after=prompt_after,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        message=terminal_message('ok') if exit_code == 0 else terminal_message('command_failed'),
    ))
