from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Users(Base):
    userid: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False, comment='业务用户ID')
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment='用户名')
    password: Mapped[str] = mapped_column(String(255), nullable=False, comment='密码（明文存储）')
