"""终端功能配置模块。

本模块集中维护终端接口、WebSocket 协议、文件传输和用户提示相关常量。
接口层只引用这些常量，不直接散落协议字符串和固定文案。
"""

# 终端默认别名；前端未传 alias 时使用。
TERMINAL_DEFAULT_ALIAS = 'terminal'

# 终端默认 Conda 环境名；会话未激活环境时使用。
TERMINAL_DEFAULT_CONDA_ENV = 'base'

# WebSocket 首包类型：前端打开或复用远程终端会话。
WS_TYPE_OPEN = 'open'

# WebSocket 输入类型：前端向远程伪终端写入普通命令或控制字符。
WS_TYPE_INPUT = 'input'

# WebSocket 前台启动类型：在当前终端会话中启动项目服务。
WS_TYPE_RUN_FOREGROUND = 'run_foreground'

# WebSocket 补全类型：在当前终端上下文中执行 Tab 补全。
WS_TYPE_COMPLETE = 'complete'

# WebSocket resize 类型：预留给前端同步终端窗口尺寸。
WS_TYPE_RESIZE = 'resize'

# WebSocket 关闭类型：前端请求关闭当前远程终端会话。
WS_TYPE_CLOSE = 'close'

# WebSocket 响应类型：终端连接就绪。
WS_RESPONSE_READY = 'ready'

# WebSocket 响应类型：终端输出流。
WS_RESPONSE_OUTPUT = 'output'

# WebSocket 响应类型：终端业务错误。
WS_RESPONSE_ERROR = 'error'

# WebSocket 响应类型：终端会话关闭。
WS_RESPONSE_CLOSED = 'closed'

# WebSocket 响应类型：前台服务已经检测到 PID/端口。
WS_RESPONSE_FOREGROUND_STARTED = 'foreground_started'

# WebSocket 响应类型：前台服务命令已发送但尚未确认端口。
WS_RESPONSE_FOREGROUND_PENDING = 'foreground_pending'

# WebSocket 响应类型：Tab 补全结果。
WS_RESPONSE_COMPLETE_RESULT = 'complete_result'

# Shell 标记：判断前台启动端口检测是否完成。
SHELL_MARK_READY = 'PSPM_READY'

# Shell 标记：回传前台启动进程 PID。
SHELL_MARK_PID = 'PSPM_PID'

# Shell 标记：普通启动流程回传运行日志路径。
SHELL_MARK_LOG = 'PSPM_LOG'

# Shell 标记：日志片段开始。
SHELL_MARK_LOG_BEGIN = 'PSPM_LOG_BEGIN'

# Shell 标记：日志片段结束。
SHELL_MARK_LOG_END = 'PSPM_LOG_END'

# 终端文件类型：目录。
TERMINAL_FILE_KIND_DIR = 'dir'

# 终端文件类型：普通文件。
TERMINAL_FILE_KIND_FILE = 'file'

# 终端文件类型：目标不存在时的内部标记。
TERMINAL_FILE_KIND_MISSING = 'missing'

# 下载目录时返回给浏览器的 media type。
TERMINAL_MEDIA_TYPE_ZIP = 'application/zip'

# 下载普通文件时返回给浏览器的 media type。
TERMINAL_MEDIA_TYPE_BINARY = 'application/octet-stream'

# 上传文件 base64 heredoc 结束标记。
TERMINAL_UPLOAD_EOF = 'PSPM_UPLOAD_EOF'

# 终端接口复用提示文案。
TERMINAL_MESSAGES = {
    'bad_command_format': '命令格式不正确，请检查引号',
    'server_ip_required': '服务器IP不能为空',
    'server_forbidden': '当前用户无该服务器使用权限',
    'session_server_id_missing': '终端会话缺少服务器ID',
    'session_not_found': '会话不存在',
    'path_outside_root': '路径超出允许的下载根目录',
    'foreground_no_port': '启动命令已进入前台运行，未配置端口，请以终端输出为准',
    'foreground_waiting_port': '启动命令已进入前台运行，正在等待端口监听',
    'ws_first_packet_required': '终端首包必须是 open',
    'terminal_session_closed': '终端会话已关闭',
    'start_command_missing': '暂无配置启动命令',
    'download_ticket_required': '下载凭证不能为空',
    'download_ticket_missing': '下载凭证不存在或已过期',
    'download_ticket_expired': '下载凭证已过期',
    'sshpass_missing_download': '当前后端未安装 sshpass/setsid，无法创建远程下载通道',
    'upload_failed': '上传失败',
    'upload_completed': '上传完成：{path}',
    'alias_required': '会话别名不能为空',
    'file_missing': '文件或目录不存在',
    'download_failed': '下载失败',
    'download_parse_failed': '下载内容解析失败',
    'command_required': '命令不能为空',
    'interactive_python_unsupported': '交互模式不支持，请使用 python --version / python -c / python 脚本.py',
    'bash_cd_missing': 'bash: cd: {target}: No such file or directory',
    'conda_env_missing': 'Conda环境不存在：{env_name}',
    'directory_missing': '目录不存在',
    'ok': 'ok',
    'command_failed': '命令执行失败',
    'unknown_error': '未知错误',
    'server_connect_failed': '连接服务器失败：{message}',
    'terminal_connection_exception': '终端连接异常：{message}',
    'server_connected': '连接成功：{server_ip}',
    'session_closed': '会话已关闭',
}

def terminal_message(key: str, **kwargs) -> str:
    """按 key 读取终端提示文案。

    参数：
    - key：`TERMINAL_MESSAGES` 中维护的文案键。
    - kwargs：文案模板需要的命名参数，例如 server_ip 或 message。

    返回：
    - str：格式化后的中文提示；不存在时返回 key 本身，便于排查错误配置。
    """
    template = TERMINAL_MESSAGES.get(key, key)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except Exception:
        return template
