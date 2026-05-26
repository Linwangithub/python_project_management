"""终端命令补全编排服务。

本模块把命令补全、路径补全、公共前缀计算组合成接口可直接返回的数据结构。
接口层只传入会话上下文和当前命令，不关心补全细节。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.utils.pspm.terminal_paths import _common_prefix, _extract_path_token, _is_command_token
from app.utils.pspm.terminal_shell import _complete_command_candidates_on_server, _complete_path_candidates_on_server


async def _build_terminal_completion_result(server_row, cwd: str, session_id: str, command: str) -> Dict[str, Any]:
    """根据当前终端上下文构建 Tab 补全返回结果。

    参数：
    - server_row：目标服务器记录。
    - cwd：当前终端工作目录。
    - session_id：终端会话 ID。
    - command：用户当前输入的命令内容。

    返回：
    - Dict[str, Any]：包含补全后命令、候选项、当前目录和状态消息。
    """
    original = str(command or '')
    prefix, token = _extract_path_token(original)

    command_candidates: List[str] = []
    if _is_command_token(prefix, token):
        command_candidates = await _complete_command_candidates_on_server(server_row, token)

    candidates = command_candidates if command_candidates else await _complete_path_candidates_on_server(server_row, cwd, token)
    if not candidates:
        return {
            'session_id': session_id,
            'requested_command': original,
            'original_command': original,
            'completed_command': original,
            'candidates': [],
            'cwd': cwd,
            'message': 'no_match',
        }

    if len(candidates) == 1:
        completed = f'{prefix}{candidates[0]}'
        if command_candidates:
            completed = f'{completed} '
    else:
        cp = _common_prefix(candidates)
        completed = f'{prefix}{cp}' if cp and cp != token else original

    return {
        'session_id': session_id,
        'requested_command': original,
        'original_command': original,
        'completed_command': completed,
        'candidates': candidates,
        'cwd': cwd,
        'message': 'ok',
    }
