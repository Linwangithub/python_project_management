import json
import os
import shlex
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app import models, schemas
from app.crud.pspm import project_status_to_name
from app.services.pspm.project_helpers import get_project_for_user, parse_conda_envs_dir
from app.utils.pspm.project_config import CONDA_INIT
from app.utils.pspm.shell_utils import _find_server_row_by_id, _list_allowed_server_rows, _run_server_shell, _split_lines


PROJECT_DETAIL_FIELD_LABELS = {
  'id': '项目ID',
  'name': '项目名称',
  'description': '项目描述',
  'owner': '所属人员',
  'server_ip': '项目服务器IP',
  'backend_path': '后端代码位置',
  'frontend_path': '前端代码位置',
  'entry_file_path': '项目入口文件位置',
  'conda_env_name': 'Conda环境名称',
  'conda_env_path': 'Conda环境位置',
  'python_version': '项目记录Python版本',
  'conda_python_version': 'Conda中的Python版本',
  'database_name': '数据库名称',
  'database_host': '数据库IP',
  'database_port': '数据库端口',
  'database_user': '数据库账号',
  'database_password': '数据库密码',
  'nginx_server_ip': 'Nginx服务器IP',
  'nginx_conf_path': 'Nginx配置文件路径',
  'frontend_port': 'Nginx前端端口',
  'backend_dev_port': '后端开发端口',
  'backend_deploy_port': '后端部署端口',
  'nginx_config_text': 'Nginx详细配置',
  'dev_start_command': '开发启动命令',
  'deploy_start_command': '部署启动命令',
  'status': '项目状态',
  'auto_start': '是否开机自启',
  'remark': '备注',
  'created_at': '创建时间',
  'updated_at': '更新时间',
}


PROJECT_DETAIL_GROUPS = [
  ('基础信息', ['id', 'name', 'description', 'owner', 'server_ip', 'status', 'created_at', 'updated_at']),
  ('路径信息', ['backend_path', 'frontend_path', 'entry_file_path']),
  ('Conda环境', ['conda_env_name', 'conda_env_path', 'python_version', 'conda_python_version']),
  ('数据库配置', ['database_name', 'database_host', 'database_port', 'database_user', 'database_password']),
  ('Nginx配置', ['nginx_server_ip', 'nginx_conf_path', 'frontend_port', 'backend_deploy_port', 'nginx_config_text']),
  ('启动配置', ['backend_dev_port', 'dev_start_command', 'deploy_start_command', 'auto_start', 'remark']),
]


CHANGE_FIELD_LABELS = PROJECT_DETAIL_FIELD_LABELS | {
  'conda_env_name': 'Conda环境',
  'dev_start_command': '开发启动命令',
  'deploy_start_command': '部署启动命令',
}


def _json_dumps(value: Any) -> str:
  """把日志详情转换成 JSON 字符串。"""
  return json.dumps(value or {}, ensure_ascii=False, default=str)


def _json_loads(value: str | None) -> dict[str, Any] | None:
  """把日志表中的 JSON 文本还原成字典。"""
  if not value:
    return None
  try:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {'value': parsed}
  except Exception:
    return {'raw': value}


def _stringify(value: Any) -> str:
  """把详情字段值转换成前端展示文本。"""
  if value is None:
    return ''
  if isinstance(value, datetime):
    return value.strftime('%Y-%m-%d %H:%M:%S')
  if isinstance(value, bool):
    return '是' if value else '否'
  return str(value)


def _has_value(value: Any) -> bool:
  """判断字段是否已经配置，空字符串和 None 不展示。"""
  return _stringify(value).strip() != ''


def _field(label: str, value: Any, *, key: str = '', mono: bool = False, secret: bool = False) -> schemas.pspm.ProjectDetailField:
  """创建一个详情字段对象。"""
  return schemas.pspm.ProjectDetailField(
    key=key,
    label=label,
    value=_stringify(value),
    mono=mono,
    secret=secret,
  )


def snapshot_project_config(project, extra: dict[str, Any] | None = None) -> dict[str, Any]:
  """生成项目配置快照。

  参数：
  - project：项目 ORM 对象。
  - extra：额外补充字段，例如服务器 IP 或 Conda 实际路径。

  作用：
  - 创建项目、修改设置、启动停止时写入操作日志。
  - 日志弹框通过该快照展示当时的详细配置。

  返回：
  - dict：项目配置字段和值。
  """
  data = {
    'id': getattr(project, 'id', None),
    'name': getattr(project, 'name', ''),
    'description': getattr(project, 'description', ''),
    'owner_id': getattr(project, 'owner_id', None),
    'server_id': getattr(project, 'server_id', None),
    'backend_path': getattr(project, 'backend_path', ''),
    'frontend_path': getattr(project, 'frontend_path', ''),
    'entry_file_path': getattr(project, 'entry_file_path', ''),
    'conda_env_name': getattr(project, 'conda_env_name', ''),
    'python_version': getattr(project, 'python_version', ''),
    'database_name': getattr(project, 'database_name', ''),
    'database_host': getattr(project, 'database_host', ''),
    'database_port': getattr(project, 'database_port', ''),
    'database_user': getattr(project, 'database_user', ''),
    'database_password': getattr(project, 'database_password', ''),
    'nginx_server_ip': getattr(project, 'nginx_server_ip', ''),
    'nginx_conf_path': getattr(project, 'nginx_conf_path', ''),
    'frontend_port': getattr(project, 'frontend_port', ''),
    'backend_dev_port': getattr(project, 'backend_dev_port', ''),
    'backend_deploy_port': getattr(project, 'backend_deploy_port', ''),
    'nginx_config_text': getattr(project, 'nginx_config_text', ''),
    'dev_start_command': getattr(project, 'dev_start_command', ''),
    'deploy_start_command': getattr(project, 'deploy_start_command', ''),
    'status': project_status_to_name(getattr(project, 'status', None)),
    'auto_start': '是' if int(getattr(project, 'auto_start', 0) or 0) == 1 else '否',
    'remark': getattr(project, 'remark', ''),
    'created_at': _stringify(getattr(project, 'created_at', None)),
    'updated_at': _stringify(getattr(project, 'updated_at', None)),
  }
  if extra:
    data.update(extra)
  return data


def build_changed_fields(before_data: dict[str, Any], after_data: dict[str, Any]) -> list[dict[str, Any]]:
  """把修改前后数据转换成“字段从什么改成什么”的列表。"""
  changes: list[dict[str, Any]] = []
  for key, after_value in after_data.items():
    before_value = before_data.get(key)
    if _stringify(before_value) == _stringify(after_value):
      continue
    changes.append({
      'key': key,
      'label': CHANGE_FIELD_LABELS.get(key, key),
      'before': _stringify(before_value),
      'after': _stringify(after_value),
    })
  return changes


async def _get_owner_name(session, owner_id: int | None) -> str:
  """查询项目所属用户名。"""
  if owner_id is None:
    return ''
  stmt = select(models.users.Users.username).where(models.users.Users.id == owner_id)
  return (await session.execute(stmt)).scalar_one_or_none() or f'user_{owner_id}'


async def _get_server_ip(session, server_id: int | None) -> str:
  """查询项目服务器 IP。"""
  if server_id is None:
    return ''
  stmt = select(models.pspm.PspmServer.ip).where(models.pspm.PspmServer.id == server_id)
  return (await session.execute(stmt)).scalar_one_or_none() or ''


async def _query_conda_detail(server_row, conda_env_name: str, stored_python_version: str) -> tuple[str, str]:
  """查询 Conda 环境实际路径和 Python 版本。"""
  env_name = str(conda_env_name or '').strip()
  if not env_name or server_row is None:
    return '', stored_python_version

  env_path = ''
  code, out, _err = await _run_server_shell(server_row, f'{CONDA_INIT}; conda env list --json', timeout=120)
  if code == 0:
    try:
      data = json.loads(out or '{}')
      envs = data.get('envs') if isinstance(data, dict) else []
      if isinstance(envs, list):
        for item in envs:
          path = str(item or '').rstrip('/')
          if os.path.basename(path) == env_name or path.endswith(f'/{env_name}'):
            env_path = path
            break
    except Exception:
      env_path = ''

  if not env_path:
    code_info, out_info, _err_info = await _run_server_shell(server_row, f'{CONDA_INIT}; conda info', timeout=120)
    if code_info == 0:
      envs_dir = parse_conda_envs_dir(out_info)
      if envs_dir:
        env_path = f'{envs_dir.rstrip("/")}/{env_name}'

  python_version = stored_python_version
  code_py, out_py, err_py = await _run_server_shell(
    server_row,
    f'{CONDA_INIT}; conda run -n {shlex.quote(env_name)} python --version',
    timeout=120,
  )
  if code_py == 0:
    py_text = ' '.join(_split_lines(out_py) + _split_lines(err_py)).strip()
    python_version = py_text or stored_python_version
  return env_path, python_version


def _build_detail_sections(values: dict[str, Any]) -> list[schemas.pspm.ProjectDetailSection]:
  """把项目详情字段按业务模块分组。"""
  sections: list[schemas.pspm.ProjectDetailSection] = []
  always_show = {'id', 'name', 'owner', 'server_ip', 'status', 'created_at', 'updated_at'}
  mono_fields = {'backend_path', 'frontend_path', 'entry_file_path', 'nginx_conf_path', 'nginx_config_text', 'dev_start_command', 'deploy_start_command', 'conda_env_path'}
  secret_fields = {'database_password'}

  for title, keys in PROJECT_DETAIL_GROUPS:
    fields: list[schemas.pspm.ProjectDetailField] = []
    for key in keys:
      value = values.get(key)
      if key not in always_show and not _has_value(value):
        continue
      fields.append(_field(
        PROJECT_DETAIL_FIELD_LABELS.get(key, key),
        value,
        key=key,
        mono=key in mono_fields,
        secret=key in secret_fields,
      ))
    if fields:
      sections.append(schemas.pspm.ProjectDetailSection(title=title, fields=fields))
  return sections


async def get_project_detail_service(session, current_user, project_id: int) -> schemas.pspm.ProjectDetailData:
  """查询项目完整详情。"""
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  owner_name = await _get_owner_name(session, getattr(project, 'owner_id', None))
  server_ip = await _get_server_ip(session, getattr(project, 'server_id', None))

  servers = await _list_allowed_server_rows(session, current_user)
  server_row = _find_server_row_by_id(servers, getattr(project, 'server_id', None))
  conda_path, conda_python_version = await _query_conda_detail(
    server_row,
    str(getattr(project, 'conda_env_name', '') or ''),
    str(getattr(project, 'python_version', '') or ''),
  )

  values = snapshot_project_config(project, {
    'owner': owner_name,
    'server_ip': server_ip,
    'conda_env_path': conda_path,
    'conda_python_version': conda_python_version,
  })
  return schemas.pspm.ProjectDetailData(
    project_id=project.id,
    project_name=project.name,
    sections=_build_detail_sections(values),
  )


async def record_project_operation(
  session,
  project,
  current_user,
  *,
  action: str,
  action_label: str,
  summary: str,
  before_data: dict[str, Any] | None = None,
  after_data: dict[str, Any] | None = None,
  detail: dict[str, Any] | None = None,
):
  """写入项目操作日志。"""
  row = models.pspm.PspmProjectOperationLog(
    project_id=int(getattr(project, 'id', 0) or 0),
    operator_id=int(getattr(current_user, 'id', 0) or 0) or None,
    operator_name=str(getattr(current_user, 'username', '') or ''),
    action=str(action or '').strip(),
    action_label=str(action_label or '').strip(),
    summary=str(summary or '').strip(),
    before_data=_json_dumps(before_data),
    after_data=_json_dumps(after_data),
    detail=_json_dumps(detail),
    status=1,
    created_by=int(getattr(current_user, 'id', 0) or 0) or 1,
  )
  session.add(row)
  await session.commit()
  await session.refresh(row)
  return row


async def list_project_logs_service(session, current_user, project_id: int) -> schemas.pspm.ProjectOperationLogsData:
  """查询项目操作日志列表。"""
  project, _is_root = await get_project_for_user(session, project_id, current_user)
  stmt = (
    select(models.pspm.PspmProjectOperationLog)
    .where(
      models.pspm.PspmProjectOperationLog.project_id == project.id,
      models.pspm.PspmProjectOperationLog.status == 1,
    )
    .order_by(models.pspm.PspmProjectOperationLog.id.desc())
  )
  rows = (await session.execute(stmt)).scalars().all()
  items = [
    schemas.pspm.ProjectOperationLogItem(
      id=row.id,
      project_id=row.project_id,
      operator_id=row.operator_id,
      operator_name=row.operator_name or '',
      action=row.action or '',
      action_label=row.action_label or '',
      summary=row.summary or '',
      before_data=_json_loads(row.before_data),
      after_data=_json_loads(row.after_data),
      detail=_json_loads(row.detail),
      created_at=row.created_at,
    )
    for row in rows
  ]

  if not items:
    creator_id = int(getattr(project, 'created_by', 0) or getattr(project, 'owner_id', 0) or 0)
    creator_name = await _get_owner_name(session, creator_id) if creator_id else ''
    server_ip = await _get_server_ip(session, getattr(project, 'server_id', None))
    items.append(
      schemas.pspm.ProjectOperationLogItem(
        id=0,
        project_id=project.id,
        operator_id=creator_id or None,
        operator_name=creator_name,
        action='create_snapshot',
        action_label='创建项目',
        summary=f'项目创建记录：{project.name}',
        before_data=None,
        after_data=snapshot_project_config(project, {'server_ip': server_ip}),
        detail={'说明': '该项目创建时操作日志功能尚未启用，当前展示的是根据项目表生成的创建快照。'},
        created_at=getattr(project, 'created_at', None),
      )
    )

  return schemas.pspm.ProjectOperationLogsData(
    project_id=project.id,
    project_name=project.name,
    total=len(items),
    data=items,
  )
