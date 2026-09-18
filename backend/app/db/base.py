from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """모든 SQLAlchemy ORM 모델이 상속하는 기반 클래스입니다."""
