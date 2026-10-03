"""
Pydantic-схемы запросов/ответов для аутентификации.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

# bcrypt учитывает только первые 72 байта пароля — длиннее не принимаем.
_PASSWORD_MAX = 72


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=_PASSWORD_MAX)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=_PASSWORD_MAX)


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class UserResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}
