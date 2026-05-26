"""Shell/SSH 工具配置模块。

用途：
- 集中维护远程命令执行相关的 SSH 选项、IP 校验规则和错误提示。
- 避免 shell_utils.py 中散落固定字符串，方便后续统一调整远程连接策略。
"""

from __future__ import annotations

import re
from typing import Final

# 允许作为服务器 IP 或主机名的安全字符，只允许字母、数字、点、冒号、下划线和短横线。
SAFE_HOST_RE: Final[re.Pattern[str]] = re.compile(r'^[A-Za-z0-9_.:-]+$')

# SSH 默认连接选项：不写 known_hosts、禁用首次确认、设置连接超时并减少噪音日志。
SSH_DEFAULT_OPTIONS: Final[str] = '-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=8 -o LogLevel=ERROR'

# SSH 无密码登录时启用 BatchMode，避免命令卡住等待输入密码。
SSH_BATCH_MODE_OPTION: Final[str] = '-o BatchMode=yes'

# Shell 输出中表示 ping 成功的统一文案。
PING_OK_MESSAGE: Final[str] = 'ok'

# Shell/SSH 工具固定错误提示。
SHELL_ERROR_MESSAGES: Final[dict[str, str]] = {
    'server_ip_required': '服务器IP不能为空',
    'server_ip_invalid': '服务器IP格式不合法：{ip}',
    'target_ip_required': '目标IP不能为空',
    'target_ip_invalid': '目标IP格式不合法：{ip}',
    'ssh_password_tool_missing': '当前后端未安装sshpass，且缺少setsid，无法使用root密码进行非交互SSH检测',
    'local_only': '当前后端仅支持本机执行，暂不支持服务器 {server_ip}',
    'ping_failed': 'ping不通',
}


def render_shell_error(key: str, **kwargs) -> str:
    """渲染 Shell/SSH 工具错误提示。

    参数：
    - key：SHELL_ERROR_MESSAGES 中的模板键。
    - kwargs：模板占位符。

    返回：
    - str：中文错误提示。
    """
    return SHELL_ERROR_MESSAGES.get(key, key).format(**kwargs)
