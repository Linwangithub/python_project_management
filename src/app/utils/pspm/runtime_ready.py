"""项目运行态检测工具模块。

用途：
- 集中维护启动后进程就绪检查和运行状态检查的远端 shell 脚本。
- runtime_utils.py 只负责启动/停止编排，本模块负责检测细节。
"""

from __future__ import annotations

import shlex
from typing import Any

from fastapi import HTTPException

from app.utils.pspm.path_utils import _safe_project_shell_script
from app.utils.pspm.runtime_helpers import (
  MSG_PROCESS_DEAD,
  MSG_RUNNING,
  MSG_START_FAIL,
  MSG_START_TIME_FAIL,
  MSG_STOPPED,
  _build_project_runtime_paths,
  _extract_marked_block,
  _extract_marked_value,
  _project_log_file,
)
from app.utils.pspm.shell_utils import _run_server_shell, _split_lines


def _service_ready_script(pid: str, port: str, log_file: str, wait_seconds: int = 20) -> str:
  """生成检测启动进程是否就绪的远端 shell 脚本。

  参数：
  - pid：启动后记录的进程 ID。
  - port：期望监听端口，可为空。
  - log_file：运行日志文件。
  - wait_seconds：最多等待秒数。

  返回：
  - 可安全包装执行的远端 shell 脚本文本。
  """
  safe_pid = shlex.quote(str(pid or ''))
  safe_port = shlex.quote(str(port or ''))
  safe_log = shlex.quote(str(log_file or ''))
  return f"""
pid={safe_pid}
port={safe_port}
log_file={safe_log}
wait_seconds={int(wait_seconds)}
if [ -z "$pid" ] || ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  echo 'PSPM_READY=0'
  echo 'PSPM_REASON=PID为空或不合法'
  exit 20
fi
for _i in $(seq 1 "$wait_seconds"); do
  if ! kill -0 "$pid" 2>/dev/null; then
    echo 'PSPM_READY=0'
    echo 'PSPM_REASON=进程已退出'
    echo 'PSPM_LOG_BEGIN'
    tail -n 120 "$log_file" 2>/dev/null || true
    echo 'PSPM_LOG_END'
    exit 21
  fi
  if [ -n "$port" ] && ss -lntH 2>/dev/null | awk '{{print $4}}' | grep -E "(^|:)$port$" >/dev/null; then
    echo 'PSPM_READY=1'
    echo 'PSPM_REASON=端口已监听'
    echo "PSPM_PORT=$port"
    echo 'PSPM_LOG_BEGIN'
    tail -n 80 "$log_file" 2>/dev/null || true
    echo 'PSPM_LOG_END'
    exit 0
  fi
  if [ -z "$port" ]; then
    detected_line="$(ss -lntpH 2>/dev/null | while IFS= read -r item; do
      item_pid="$(echo "$item" | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2)"
      [ "$item_pid" = "$pid" ] || continue
      echo "$item"
      break
    done | head -n 1)"
    if [ -n "$detected_line" ]; then
      detected_port="$(echo "$detected_line" | awk '{{print $4}}' | awk -F: '{{print $NF}}' | grep -E '^[0-9]+$' | head -n 1)"
      if [ -n "$detected_port" ]; then
        echo 'PSPM_READY=1'
        echo 'PSPM_REASON=进程端口已监听'
        echo "PSPM_PORT=$detected_port"
        echo 'PSPM_LOG_BEGIN'
        tail -n 80 "$log_file" 2>/dev/null || true
        echo 'PSPM_LOG_END'
        exit 0
      fi
    fi
  fi
  sleep 1
done
echo 'PSPM_READY=0'
echo 'PSPM_REASON=等待服务监听端口超时'
echo 'PSPM_LOG_BEGIN'
tail -n 120 "$log_file" 2>/dev/null || true
echo 'PSPM_LOG_END'
exit 23
"""


async def _check_started_process_ready(
  *,
  server_row,
  project_id: int,
  pid: str,
  port: str,
  log_file: str | None = None,
  wait_seconds: int = 20,
) -> dict[str, Any]:
  """启动后等待进程和端口达到可用状态。

  作用：
  - 检查 PID 是否存活。
  - 在配置端口时等待端口监听。
  - 失败时返回运行日志片段辅助定位。

  返回：
  - ready、reason、log_output、stdout、stderr 等检查结果。
  """
  log_file = str(log_file or '').strip() or _project_log_file(project_id)
  script = _service_ready_script(pid, port, log_file, wait_seconds)
  code, out, err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=wait_seconds + 10)
  ready = _extract_marked_value(out, 'PSPM_READY') == '1'
  reason = _extract_marked_value(out, 'PSPM_REASON') or (err.strip() or out.strip() or '')
  detected_port = _extract_marked_value(out, 'PSPM_PORT') or str(port or '').strip()
  log_output = _extract_marked_block(out, 'PSPM_LOG_BEGIN', 'PSPM_LOG_END')
  return {
    'ready': ready,
    'reason': reason,
    'port': detected_port,
    'log_output': log_output,
    'stdout': out,
    'stderr': err,
    'exit_code': code,
    'log_file': log_file,
  }



async def _inspect_project_runtime(server_row, project) -> dict[str, str]:
  """检查项目当前服务状态和实际运行端口。

  参数：
  - server_row：项目所在服务器记录。
  - project：项目 ORM 对象。

  返回：
  - `service_status` 和 `running_port`。
  """
  if not server_row:
    return {'service_status': MSG_STOPPED, 'running_port': ''}

  _runtime_dir, pid_file, meta_file = _build_project_runtime_paths(project.id)
  script = f"""
set -euo pipefail
pid_file={shlex.quote(pid_file)}
meta_file={shlex.quote(meta_file)}
if [ ! -f "$pid_file" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
pid="$(cat "$pid_file" 2>/dev/null || true)"
meta="$(cat "$meta_file" 2>/dev/null || true)"
if ! echo "$pid" | grep -Eq '^[0-9]+$'; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
if [ ! -d "/proc/$pid" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
meta_pid="$(echo "$meta" | awk -F'|' '{{print $1}}')"
meta_start="$(echo "$meta" | awk -F'|' '{{print $2}}')"
meta_port="$(echo "$meta" | awk -F'|' '{{print $5}}')"
if [ -n "$meta_pid" ] && [ "$meta_pid" != "$pid" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
if [ -n "$meta_start" ]; then
  current_start="$(awk '{{print $22}}' /proc/$pid/stat 2>/dev/null || true)"
  if [ -n "$current_start" ] && [ "$current_start" != "$meta_start" ]; then
    echo "service_status={MSG_STOPPED}"
    echo "running_port="
    exit 0
  fi
fi
port="$meta_port"
if ! echo "$port" | grep -Eq '^[0-9]+$'; then
  port=""
fi
if [ -z "$port" ]; then
  port="$(ss -lntpH 2>/dev/null | while IFS= read -r item; do
    item_pid="$(echo "$item" | grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2)"
    [ "$item_pid" = "$pid" ] || continue
    echo "$item" | awk '{{print $4}}' | awk -F: '{{print $NF}}' | grep -E '^[0-9]+$'
    break
  done | head -n 1)"
fi
if [ -z "$port" ]; then
  echo "service_status={MSG_STOPPED}"
  echo "running_port="
  exit 0
fi
echo "service_status={MSG_RUNNING}"
echo "running_port=$port"
"""
  code, out, _err = await _run_server_shell(server_row, _safe_project_shell_script(script), timeout=10)
  result = {'service_status': MSG_STOPPED, 'running_port': ''}
  if code != 0:
    return result
  for line in _split_lines(out):
    if '=' not in line:
      continue
    key, value = line.split('=', 1)
    key = key.strip()
    if key in result:
      result[key] = value.strip()
  return result
