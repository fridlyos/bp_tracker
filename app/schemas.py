from pydantic import BaseModel


class User(BaseModel):
    id: int
    username: str
    is_admin: bool = False
    created_at: str | None = None
