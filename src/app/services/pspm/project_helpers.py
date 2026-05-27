"""项目服务辅助模块，提供项目配置快照、日志记录和差异描述等复用能力。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

import os
import shlex

from fastapi import HTTPException

from app import crud, schemas
from app.utils.pspm.path_utils import _safe_conda_name
from app.utils.pspm.conda_utils import run_conda_command_on_server
from app.utils.pspm.project_config import FORBIDDEN_PROJECT_DELETE_PATHS, FRONTEND_DIST_BASE_DIR
from app.utils.pspm.shell_utils import _run_server_shell, _split_lines


def frontend_dist_base_dir_for_user(current_user, is_root: bool) -> str:
  """返回当前用户对应的前端打包根目录。

  参数：
  - current_user：登录用户对象，由接口层的 `require_permission` 依赖注入得到。
  - is_root：当前用户是否为 root 角色，由 `crud.rbac.is_root_user` 查询得到。

  作用：
  - 创建或设置 Nginx 时，Nginx server block 的 `root` 需要指向前端打包目录。
  - 所有用户统一使用配置中的前端打包资源根目录，避免 Nginx 访问用户私有目录权限受限。
  - 普通用户也使用同一公共静态资源根目录，项目名仍作为下一级目录。

  返回：
  - 字符串形式的前端打包根目录。

  被使用位置：
  - `project_create.create_project_real_service`
  - `project_setting.update_project_setting_service`
  """
  username = str(getattr(current_user, 'username', '') or 'root').strip() or 'root'
  safe_username = ''.join(ch if (ch.isalnum() or ch in {'_', '-'}) else '_' for ch in username)
  return FRONTEND_DIST_BASE_DIR


def frontend_root_for_project(current_user, is_root: bool, project_name: str) -> str:
  """返回某个项目对应的前端打包目录。

  参数：
  - current_user：登录用户对象。
  - is_root：当前用户是否为 root。
  - project_name：项目名称，来自创建项目或项目记录。

  作用：
  - 在 Nginx 配置模板中生成 `root` 路径。
  - 在项目表 `frontend_path` 字段中保存该项目的前端打包路径。

  返回：
  - “前端打包资源根目录/项目名”。
  """
  base_dir = frontend_dist_base_dir_for_user(current_user, is_root)
  safe_project_name = str(project_name or '').strip().strip('/')
  return f'{base_dir}/{safe_project_name}' if safe_project_name else base_dir


def verify_project_owner(project, current_user: schemas.users.Data, is_root: bool):
  """校验当前用户是否有权限操作指定项目。

  参数：
  - project：项目 ORM 对象，由项目 ID 查询得到。
  - current_user：当前登录用户。
  - is_root：当前用户是否为 root 角色。

  作用：
  - root 可以操作所有项目。
  - 普通用户只能操作 `owner_id` 等于自己的项目。

  返回：
  - 校验通过时无返回值。

  异常：
  - 无权限时抛出 HTTP 403。

  被使用位置：
  - 项目入口文件浏览、Conda 环境列表、端口检测、设置、启动、停止、复制、导出、删除等接口。
  """
  if (not is_root) and (project.owner_id != current_user.id):
    raise HTTPException(status_code=403, detail='无权限')


def parse_conda_envs_dir(conda_info_text: str) -> str:
  """从 `conda info` 输出中解析 Conda 环境目录。

  参数：
  - conda_info_text：远端服务器执行 `conda info` 后返回的 stdout 文本。

  作用：
  - 前端设置项目时，需要下拉展示服务器已有 Conda 环境。
  - 本函数从 `envs directories` 这一行解析 Conda 环境目录。

  返回：
  - 解析成功返回实际 Conda 环境目录。
  - 解析失败返回空字符串，由调用方决定如何报错。

  被使用位置：
  - `list_conda_env_names_on_server`
  - `project_checks.list_project_conda_envs_service`
  """
  lines = _split_lines(conda_info_text)
  for idx, line in enumerate(lines):
    text = str(line or '').rstrip()
    stripped = text.strip()
    if not stripped.startswith('envs directories'):
      continue
    parts = stripped.split(':', 1)
    if len(parts) == 2 and parts[1].strip():
      return parts[1].strip().split()[0]
    if idx + 1 < len(lines):
      candidate = str(lines[idx + 1] or '').strip().split()
      if candidate:
        return candidate[0]
  return ''


async def list_conda_env_names_on_server(server_row, envs_dir: str | None = None) -> list[str]:
  """查询某台服务器上的 Conda 环境名称列表。

  参数：
  - server_row：服务器 ORM 对象，通常由 `_find_server_row_by_id` 或 `_find_server_row_by_ip` 得到。

  作用：
  - 先执行 `conda info` 找到 `envs directories`。
  - 再 `find` 该目录下一层文件夹，得到已有环境名。
  - 用于设置项目时判断新 Conda 环境是否已存在，以及给前端下拉框提供选项。

  返回：
  - Conda 环境名称列表，例如 `['base', 'project_management']`。

  异常：
  - 查询失败、解析失败、列目录失败时抛出 HTTP 500。
  """
  if not envs_dir:
    code, out, err = await run_conda_command_on_server(server_row, 'conda info', timeout=120)
    if code != 0:
      raise HTTPException(status_code=500, detail=f'查询Conda信息失败：{err.strip() or out.strip() or '未知错误'}')

    envs_dir = parse_conda_envs_dir(out)
    if not envs_dir:
      raise HTTPException(status_code=500, detail='未解析到Conda环境目录')

  safe_envs_dir = shlex.quote(envs_dir)
  code_ls, out_ls, err_ls = await _run_server_shell(
    server_row,
    f'if [ -d {safe_envs_dir} ]; then find {safe_envs_dir} -mindepth 1 -maxdepth 1 -type d -printf "%f\n" | sort; fi',
    timeout=120,
  )
  if code_ls != 0:
    raise HTTPException(status_code=500, detail=f'查询Conda环境列表失败：{err_ls.strip() or out_ls.strip() or '未知错误'}')
  return [x.strip() for x in _split_lines(out_ls) if x.strip()]


async def get_project_for_user(session, project_id: int, current_user):
  """查询项目并校验当前用户是否可访问。

  参数：
  - session：数据库会话，由接口层 `SessionDep` 注入。
  - project_id：项目 ID，来自 Query 参数或业务调用。
  - current_user：当前登录用户。

  作用：
  - 多个接口都需要“查询项目 + 判断项目存在 + 判断权限”。
  - 集中封装后避免每个接口重复写同样代码。

  返回：
  - `(project, is_root)`：
    - project：项目 ORM 对象。
    - is_root：当前用户是否为 root。

  异常：
  - 项目不存在时抛出 HTTP 404。
  - 无权限时抛出 HTTP 403。
  """
  project = await crud.projects.get(session, obj_in={'id': project_id, 'status': [0, 1]})
  if not project:
    raise HTTPException(status_code=404, detail='项目不存在')

  is_root = await crud.rbac.is_root_user(session, user_id=current_user.id)
  verify_project_owner(project, current_user, is_root)
  return project, is_root


def ensure_safe_project_delete_path(path: str) -> str:
  """校验项目目录删除路径是否安全。

  参数：
  - path：项目后端目录，来自项目表 `backend_path` 字段。

  作用：
  - 删除项目时会执行 `rm -rf`，必须禁止删除根目录、用户主目录、项目根目录等高危路径。
  - 保证只删除具体项目目录，避免误删系统目录。

  返回：
  - 标准化后的绝对路径。

  异常：
  - 路径为空、不安全、层级过浅时抛出 HTTP 400。

  被使用位置：
  - `project_delete.delete_project_service`
  """
  backend_path = os.path.normpath(str(path or '').strip()) if path else ''
  if not backend_path:
    return ''
  if (not backend_path.startswith('/')) or backend_path in FORBIDDEN_PROJECT_DELETE_PATHS:
    raise HTTPException(status_code=400, detail=f'项目路径不安全，拒绝删除：{backend_path}')
  if backend_path.count('/') < 2:
    raise HTTPException(status_code=400, detail=f'项目路径层级过浅，拒绝删除：{backend_path}')
  return backend_path


def safe_existing_conda_name(name: str | None) -> str:
  """安全读取项目原 Conda 环境名。

  参数：
  - name：项目表中的 `conda_env_name` 字段值。

  作用：
  - 设置项目时如果用户要求删除原 Conda 环境，需要先安全校验原环境名。

  返回：
  - 原环境名为空时返回空字符串。
  - 非空时返回 `_safe_conda_name` 校验后的环境名。
  """
  return _safe_conda_name(str(name or '')) if name else ''
