from sqlalchemy import Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from sourcetrace.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeBase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_bases"
    name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (Index("uq_knowledge_bases_name_ci", func.lower(name), unique=True),)
