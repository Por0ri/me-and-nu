from sqlalchemy.types import UserDefinedType


class Vector(UserDefinedType):
    """pgvector의 차원 미지정 VECTOR 타입입니다."""

    cache_ok = True

    def get_col_spec(self, **_: object) -> str:
        return "VECTOR"
