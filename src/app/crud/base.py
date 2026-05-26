"""基础 CRUD 模块，封装模型通用增删改查能力。

本模块只维护本文件所属层级的职责，避免接口、服务、工具和配置逻辑互相混杂。
"""

from typing import Any, Dict, Generic, Optional, Type, TypeVar, Union, List, Literal
from pydantic import BaseModel
from sqlalchemy.sql import func, delete, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app import models

ModelType = TypeVar("ModelType", bound=models.base.Base)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)


class CRUDBase(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """通用 CRUD 基类。

    封装列表、详情、创建、更新、删除等数据库操作。
    """
    def __init__(self, model: Type[ModelType]):
        """初始化 CRUD 基类。

        参数：
        - model：SQLAlchemy 模型类，用于后续通用数据库操作。
        """
        self.model = model

    def filters(self, obj_in: Optional[Dict[str, Any]] = None) -> List[Any]:
        """
        构建查询条件
        :param obj_in: 查询参数
        :return: 查询条件
        """
        conditions = []
        if obj_in:
            for key, value in obj_in.items():
                if value is None:
                    continue
                if isinstance(value, list):
                    # 数组类型使用in_操作符
                    conditions.append(getattr(self.model, key).in_(value))
                else:
                    # 普通类型使用相等匹配
                    conditions.append(getattr(self.model, key) == value)
        return conditions

    def values(self, obj_in: Dict[str, Any]) -> Dict[str, Any]:
        """
        构建更新值字典
        :param obj_in: 包含要更新字段和值的字典
        :return: 处理后的更新值字典，可直接用于update语句的values方法
        """
        update_values = {}

        if obj_in:
            for key, value in obj_in.items():
                # 检查是否是模型中存在的字段
                if hasattr(self.model, key):
                    # 获取字段类型
                    column = getattr(self.model, key)

                    # 特殊类型处理示例：
                    # 1. 处理None值（根据业务需求决定是否需要）
                    # 2. 处理日期时间类型
                    # 3. 处理枚举类型
                    # 这里可以根据实际业务需求添加更多类型转换逻辑

                    # 默认情况，直接使用值
                    if value is not None:
                        update_values[key] = value

        return update_values

    async def is_exist(self, db: AsyncSession, obj_in: Dict[str, Any]) -> bool:
        """
        检查记录是否存在
        :param db: 数据库会话
        :param obj_in: 查询条件
        :return: 如果存在则返回True，否则返回False
        """
        query = select(func.count()).select_from(self.model)
        conditions = self.filters(obj_in)
        if conditions:
            query = query.filter(*conditions)
        result = await db.execute(query)
        return result.scalar_one() > 0

    async def get(self, db: AsyncSession, obj_in: Dict[str, Any]) -> Optional[ModelType]:
        """
        按条件查询单条模型记录。
        
        参数：
        - db：数据库会话。
        - obj_in：过滤条件字典。
        
        返回：
        - 匹配到的 ORM 对象；不存在时返回 None。
        """
        try:
            query = select(self.model)
            conditions = self.filters(obj_in)
            if conditions:
                query = query.filter(*conditions)
            result = await db.execute(query)
            return result.scalar_one_or_none()
        except SQLAlchemyError as e:
            raise e

    async def get_total(self, db: AsyncSession, obj_in: Optional[Dict[str, Any]] = None) -> int:
        """
        按条件统计模型记录总数。
        
        参数：
        - db：数据库会话。
        - obj_in：过滤条件字典，可为空。
        
        返回：
        - 匹配条件的记录数量。
        """
        try:
            query = select(func.count()).select_from(self.model)
            conditions = self.filters(obj_in)
            if conditions:
                query = query.filter(*conditions)
            result = await db.execute(query)
            return result.scalar_one()
        except SQLAlchemyError as e:
            raise e

    async def get_multi(self, db: AsyncSession, *, obj_in: Optional[Dict[str, Any]] = None, page: int = 1, page_size: int = 20, page_break: bool = False, sort: Literal['asc', 'desc'] = 'desc') -> List[ModelType]:
        """
        按条件分页查询模型记录列表。
        
        参数：
        - db：数据库会话。
        - obj_in：过滤条件字典。
        - page/page_size：分页参数。
        - page_break：为 True 时不分页。
        - sort：按 id 升序或降序。
        
        返回：
        - ORM 对象列表。
        """
        try:
            query = select(self.model)
            conditions = self.filters(obj_in)
            if conditions:
                query = query.filter(*conditions)
            if not page_break:
                if page > 0:
                    query = query.offset((page - 1) * page_size)
                if sort == 'asc':
                    query = query.order_by(self.model.id.asc())
                else:
                    query = query.order_by(self.model.id.desc())

                query = query.limit(page_size)
            result = await db.execute(query)
            return result.scalars().all()
        except SQLAlchemyError as e:
            raise e

    async def create(self, db: AsyncSession, *, obj_in: Union[List[Dict[str, Any]], Dict[str, Any]]) -> Union[List[ModelType], ModelType]:
        """
        创建记录
        :param db: 数据库会话
        :param obj_in: 创建记录的数据列表
        :return: 创建的模型对象列表
        """
        try:
            if isinstance(obj_in, list):
                db_objs = []
                for item in obj_in:
                    create_values = self.values(item)
                    db_obj = self.model(**create_values)  # type: ignore
                    db_objs.append(db_obj)
                    db.add(db_obj)
                await db.commit()
                for db_obj in db_objs:
                    await db.refresh(db_obj)
                return db_objs
            else:
                create_values = self.values(obj_in)
                db_obj = self.model(**create_values)  # type: ignore
                db.add(db_obj)
                await db.commit()
                await db.refresh(db_obj)
                return db_obj
        except SQLAlchemyError as e:
            await db.rollback()
            raise e

    async def update(self, db: AsyncSession, *, obj_in: Dict[str, Any], data_in: Dict[str, Any]) -> Optional[int]:
        """
        更新符合条件的记录
        :param db: 异步数据库会话
        :param obj_in: 过滤条件字典
        :param data_in: 要更新的字段和值的字典
        :return: 更新的记录数量，如果没有更新则返回None
        :raises SQLAlchemyError: 当数据库操作失败时
        """
        try:
            query = update(self.model)
            conditions = self.filters(obj_in)
            if conditions:
                query = query.filter(*conditions)
            update_values = self.values(data_in)
            if update_values:
                query = query.values(**update_values)
                # 添加返回受影响的行数
                query = query.execution_options(synchronize_session="fetch")

                result = await db.execute(query)
                await db.commit()

                return result.rowcount
            return None
        except SQLAlchemyError as e:
            await db.rollback()
            raise e

    async def remove(self, db: AsyncSession, *, obj_in: Dict[str, Any]) -> Optional[int]:
        """
        删除符合条件的记录
        :param db: 异步数据库会话
        :param obj_in: 过滤条件字典
        :return: 删除的记录数量，如果没有删除则返回None
        :raises SQLAlchemyError: 当数据库操作失败时
        """
        try:
            query = delete(self.model)
            conditions = self.filters(obj_in)
            if conditions:
                query = query.filter(*conditions)
            # 添加返回受影响的行数
            query = query.execution_options(synchronize_session="fetch")
            result = await db.execute(query)
            await db.commit()
            return result.rowcount
        except SQLAlchemyError as e:
            await db.rollback()
            raise e

    async def soft_remove(self, db: AsyncSession, *, obj_in: Dict[str, Any]) -> Optional[int]:
        """
        软删除符合条件的记录
        :param db: 异步数据库会话
        :param obj_in: 过滤条件字典
        :return: 删除的记录数量，如果没有删除则返回None
        :raises SQLAlchemyError: 当数据库操作失败时
        """
        return await self.update(db, obj_in=obj_in, data_in={'status': -1})

    async def upsert(self, db: AsyncSession, *, obj_in: Dict[str, Any], data_in: Dict[str, Any]) -> Union[ModelType, Optional[int]]:
        """
        根据查询条件执行upsert操作：如果记录存在则更新，不存在则创建
        :param db: 数据库会话
        :param obj_in: 查询条件字典
        :param data_in: 要插入或更新的数据字典
        :return: 创建的模型对象或更新的记录数量
        """
        # 检查记录是否存在
        existing_record = await self.get(db, obj_in)
        if existing_record:
            # 记录存在，执行更新操作
            is_update = False
            for k, v in data_in.items():
                if k in existing_record.__dict__ and (v != getattr(existing_record, k)):
                    is_update = True
            if is_update:
                return await self.update(db, obj_in=obj_in, data_in=data_in)
        else:
            # 记录不存在，执行创建操作
            # 合并查询条件和数据字典以创建完整的记录
            exclude_fields = ['id', 'created_at', 'updated_at']
            for field in exclude_fields:
                if field in obj_in:
                    del obj_in[field]
            create_data = {**obj_in, **data_in}
            return await self.create(db, obj_in=create_data)
