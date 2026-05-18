import os

from fastapi import HTTPException

from app.utils.pspm.project_config import PORT_MAX, PORT_MIN, SAFE_ENV_NAME_RE


def _normalize_path(path: str) -> str:
  """校验并标准化绝对路径。

  参数：
  - path：前端或数据库传入的路径字符串。

  作用：
  - 项目创建、入口文件浏览、项目启动等场景都需要确认路径是 Linux 绝对路径。

  返回：
  - 标准化后的绝对路径。
  """
  value = (path or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='项目路径不能为空')
  normalized = os.path.normpath(value)
  if not normalized.startswith('/'):
    raise HTTPException(status_code=400, detail='项目路径必须是绝对路径')
  return normalized


def _safe_project_name(name: str) -> str:
  """校验项目名称。

  参数：
  - name：项目名称，来自创建项目或检查项目名接口。

  作用：
  - 禁止空项目名和带路径分隔符的项目名，避免把项目创建到意外目录。

  返回：
  - 去掉首尾空白后的项目名称。
  """
  value = (name or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='项目名称不能为空')
  if '/' in value or '\\' in value:
    raise HTTPException(status_code=400, detail='项目名称不能包含路径分隔符')
  return value


def _safe_python_version(version: str) -> str:
  """校验 Python 版本输入。

  参数：
  - version：Python 版本，例如 `3.10`。

  作用：
  - 创建 Conda 环境前确保版本不为空。

  返回：
  - 去掉首尾空白后的版本字符串。
  """
  value = (version or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='Python版本不能为空')
  return value


def _safe_conda_name(name: str) -> str:
  """校验 Conda 环境名称。

  参数：
  - name：Conda 环境名，通常与项目名一致，也可在设置中修改。

  作用：
  - 防止空值、空格、特殊字符进入 shell 命令。

  返回：
  - 合法 Conda 环境名。
  """
  value = (name or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='Conda环境名不能为空')
  if not SAFE_ENV_NAME_RE.match(value):
    raise HTTPException(status_code=400, detail='Conda环境名仅支持字母、数字、点、下划线、短横线')
  return value


def _safe_command(command: str, label: str) -> str:
  """校验启动命令。

  参数：
  - command：前端填写的启动命令。
  - label：字段中文名，用于错误提示。

  作用：
  - 防止启动命令为空或包含换行。

  返回：
  - 去掉首尾空白后的命令。
  """
  value = (command or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail=f'{label}不能为空')
  if '\n' in value or '\r' in value:
    raise HTTPException(status_code=400, detail=f'{label}不能包含换行')
  return value


def _safe_entry_file_path(path: str) -> str:
  """校验项目入口文件相对路径。

  参数：
  - path：入口文件相对项目根目录的路径，例如 `main.py` 或 `src/app.py`。

  作用：
  - 设置项目启动配置时，入口文件必须在项目目录内。

  返回：
  - 标准化后的相对路径。
  """
  value = (path or '').strip()
  if not value:
    raise HTTPException(status_code=400, detail='项目入口文件位置不能为空')

  if os.path.isabs(value):
    raise HTTPException(status_code=400, detail='项目入口文件位置必须是相对路径')

  normalized = os.path.normpath(value).replace('\\', '/')
  if normalized in {'.', ''}:
    raise HTTPException(status_code=400, detail='项目入口文件位置不能为空')
  if normalized.startswith('../') or normalized == '..':
    raise HTTPException(status_code=400, detail='项目入口文件位置不合法')
  return normalized


def _safe_rel_path_input(path: str) -> str:
  """校验入口文件浏览器传入的相对目录。

  参数：
  - path：前端级联选择器当前相对目录。

  作用：
  - 防止使用绝对路径或 `..` 越界访问项目外部目录。

  返回：
  - 标准化后的相对目录；空输入返回空字符串。
  """
  value = (path or '').strip()
  if not value:
    return ''
  normalized = os.path.normpath(value).replace('\\', '/')
  if normalized in {'.', ''}:
    return ''
  if os.path.isabs(normalized):
    raise HTTPException(status_code=400, detail='路径不合法')
  if normalized.startswith('../') or normalized == '..':
    raise HTTPException(status_code=400, detail='路径不合法')
  return normalized


def _resolve_entry_browser_abs_path(project_backend_path: str, rel_path: str) -> str:
  """把入口文件浏览相对路径解析为绝对路径。

  参数：
  - project_backend_path：项目后端根目录，来自项目表 `backend_path`。
  - rel_path：前端传入的相对目录。

  作用：
  - 入口文件选择器需要读取实际文件系统目录。
  - 解析时会再次确认目标路径没有越过项目根目录。

  返回：
  - 可安全读取的绝对目录路径。
  """
  base = _normalize_path(project_backend_path)
  rel = _safe_rel_path_input(rel_path)
  if not rel:
    return base
  target = os.path.normpath(os.path.join(base, rel))
  if not target.startswith(f'{base}/') and target != base:
    raise HTTPException(status_code=400, detail='路径越界')
  return target


def _build_target_dir(base_path: str, project_name: str) -> str:
  """拼接项目最终目录。

  参数：
  - base_path：项目基础目录，例如 `/root/project`。
  - project_name：项目名称。

  返回：
  - 标准化后的项目目录。
  """
  return os.path.normpath(os.path.join(base_path, project_name))


def _safe_project_shell_script(script: str) -> str:
  """清理项目 shell 脚本文本。

  参数：
  - script：启动或部署脚本文本。

  返回：
  - 去掉首尾空白后的脚本文本。
  """
  text = str(script or '')
  return text.strip()


def _safe_optional_port_text(value: str | None) -> str:
  """校验可选端口文本。

  参数：
  - value：前端输入的端口字符串，可以为空。

  作用：
  - 开发端口、部署端口、Nginx 前端端口在部分步骤中允许为空。
  - 非空时必须是 1024-49151 范围内的数字。

  返回：
  - 空输入返回空字符串；非空返回原端口文本。
  """
  text = str(value or '').strip()
  if not text:
    return ''
  if not text.isdigit():
    raise HTTPException(status_code=400, detail='端口必须为数字')
  num = int(text)
  if num < PORT_MIN or num > PORT_MAX:
    raise HTTPException(status_code=400, detail=f'端口范围不合法（{PORT_MIN}-{PORT_MAX}）')
  return text


def _safe_port_number(port: int) -> int:
  """校验必填端口数字。

  参数：
  - port：端口数字。

  作用：
  - 端口检测接口和 Nginx 配置保存时使用。

  返回：
  - 合法端口数字。
  """
  if port < PORT_MIN or port > PORT_MAX:
    raise HTTPException(status_code=400, detail=f'端口范围不合法（{PORT_MIN}-{PORT_MAX}）')
  return int(port)
