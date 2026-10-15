"""
FastAPI-зависимости для аутентификации и авторизации.

get_allowed_project — единая точка принятия решения о доступе к проекту.
В MVP правило простое (admin видит всё, иначе только свой project.owner_id),
но весь остальной код обращается именно к этой функции, а не сравнивает id напрямую —
это то место, которое поменяется, когда появится таблица project_members и роли внутри проекта.

Репозитории приходят через порты IProjectRepository / IUserRepository из
core/dependencies.py, JWTHandler — через Depends(get_jwt_handler), один на процесс.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.dependencies import get_jwt_handler, get_project_repository, get_user_repository
from app.domain.exceptions import InvalidTokenError
from app.domain.interfaces.entities import UserProtocol
from app.domain.interfaces.repositories import IProjectRepository, IUserRepository
from app.domain.interfaces.security import ITokenIssuer
from app.infrastructure.db.models.enums import UserRole
from app.infrastructure.db.models.project import Project

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    user_repository: IUserRepository = Depends(get_user_repository),
    jwt_handler: ITokenIssuer = Depends(get_jwt_handler),
) -> UserProtocol:
    try:
        payload = jwt_handler.decode_access_token(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user = await user_repository.get_by_id(uuid.UUID(payload["sub"]))
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден"
        )
    return user


def require_admin(current_user: UserProtocol = Depends(get_current_user)) -> UserProtocol:
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Требуются права администратора"
        )
    return current_user


async def get_allowed_project(
    project_id: uuid.UUID,
    current_user: UserProtocol = Depends(get_current_user),
    project_repository: IProjectRepository = Depends(get_project_repository),
) -> Project:
    project = await project_repository.get_by_id(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден")

    # Чужой проект неотличим от несуществующего: иначе по коду ответа можно
    # перебирать идентификаторы.
    if current_user.role != UserRole.ADMIN and project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Проект не найден")

    return project
