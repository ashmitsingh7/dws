"""
auth_api.py
============

FastAPI router for the DWS authentication / RBAC subsystem.

This module defines an APIRouter (NOT a standalone FastAPI() app) so
it can be mounted into the existing api_server.py with a single line:

    from authentication.auth_api import auth_router
    app.include_router(auth_router)

It deliberately does not redefine CORS, does not create its own
FastAPI instance, and does not touch any existing route in
api_server.py. See AUTHENTICATION_ARCHITECTURE.md ("Integration"
section) for the exact one-line wiring needed in api_server.py
itself, which is left to whichever agent/session owns that file.

Route namespace
----------------
api_server.py already has a route at GET /session (shift/session
*statistics*, unrelated to login). To avoid any collision, every
route in this module lives under /auth or /admin:

    POST   /auth/login
    POST   /auth/logout
    GET    /auth/session            <- "who am I" / token introspection
    POST   /auth/change-password    <- self-service, requires current password

    GET    /admin/users
    POST   /admin/users
    PUT    /admin/users/{id}
    DELETE /admin/users/{id}
    POST   /admin/users/{id}/enable
    POST   /admin/users/{id}/disable
    POST   /admin/users/{id}/reset-password

    GET    /admin/roles

Connection handling matches api_server.py's existing get_conn()
pattern: a short-lived sqlite3.Connection per request rather than a
pool, since this is a low-throughput operator-facing API (warehouse
floor logins, not a public web service) and the existing codebase
has no connection-pooling infrastructure to plug into.
"""

from __future__ import annotations

import os
import sys
import sqlite3
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

from auth_manager import (  # noqa: E402
    AuthManager,
    InvalidCredentialsError,
    AccountDisabledError,
    AccountLockedError,
    RateLimitedError,
)
from session_manager import SessionInvalid, SessionExpired  # noqa: E402
from user_manager import (  # noqa: E402
    UserManagerError,
    UsernameTakenError,
    UserNotFoundError,
    UnknownRoleError,
    InsufficientPrivilegeError,
    LastAdministratorError,
)
from password_manager import PasswordPolicyError  # noqa: E402
from permissions import (  # noqa: E402
    Principal,
    has_permission,
    PERM_USERS_MANAGE,
    PERM_ROLES_MANAGE,
    DEFAULT_ROLES,
    ALL_PERMISSIONS,
    PERMISSION_DESCRIPTIONS,
    ROLE_PERMISSIONS,
)

try:
    from dws_ros2.paths import DB_PATH
except ImportError:
    DB_PATH = os.environ.get("DWS_DB_PATH", os.path.expanduser("~/dws_data.db"))


auth_router = APIRouter()

# ---------------------------------------------------------------------------
# Process-wide IP rate limiter singleton
#
# AuthManager owns an _IpRateLimiter instance. Because this API uses a
# short-lived sqlite3.Connection per request (matching api_server.py's
# existing get_conn() pattern), a naïve "new AuthManager(conn) per
# request" would silently create a NEW _IpRateLimiter on each request,
# resetting every IP's failure counter between requests and making the
# IP rate limit completely ineffective.
#
# Fix: one _IpRateLimiter is created at module load time and shared by
# every AuthManager instance in this process. AuthManager.__init__ is
# patched below to accept an optional pre-built limiter so the limiter
# itself never needs to be aware of the connection lifecycle.
# ---------------------------------------------------------------------------

from auth_manager import _IpRateLimiter as _IpRateLimiterClass  # noqa: E402
from auth_manager import (  # re-import to shadow the already-imported names  # noqa: E402,F811
    IP_RATE_LIMIT_MAX_ATTEMPTS,
    IP_RATE_LIMIT_WINDOW_SECONDS,
    IP_RATE_LIMIT_BLOCK_SECONDS,
)

_process_ip_limiter = _IpRateLimiterClass(
    IP_RATE_LIMIT_MAX_ATTEMPTS,
    IP_RATE_LIMIT_WINDOW_SECONDS,
    IP_RATE_LIMIT_BLOCK_SECONDS,
)


def _make_auth_manager(conn) -> "AuthManager":
    """Create an AuthManager that reuses the process-wide IP rate limiter."""
    am = AuthManager(conn)
    am._ip_limiter = _process_ip_limiter
    return am


# ---------------------------------------------------------------------------
# Connection helper — mirrors api_server.py's get_conn()
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    # Resolve DB_PATH at call time (not at import time) so that tests which
    # monkeypatch DWS_DB_PATH and re-import auth_api get the right database
    # even when dws_ros2.paths is already cached in sys.modules.
    env_override = os.environ.get("DWS_DB_PATH")
    if env_override:
        db_path = env_override
    else:
        try:
            from dws_ros2.paths import DB_PATH as _dp
            db_path = _dp
        except ImportError:
            db_path = DB_PATH  # module-level fallback
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _client_ip(request: Request) -> str | None:
    """
    Best-effort client IP extraction. Checks X-Forwarded-For first
    (in case a reverse proxy sits in front of uvicorn in some
    deployments — see ELECTRON_ARCHITECTURE.md, which notes the
    backend may run standalone or under systemd) and falls back to
    the direct socket peer.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _require_principal(authorization: Optional[str], conn: sqlite3.Connection) -> Principal:
    """
    Extracts a bearer token from the Authorization header and
    validates it. Raises HTTPException(401) on any failure — used as
    a plain function call at the top of protected routes rather than
    a FastAPI Depends(), purely to match this codebase's existing
    style of explicit checks inside the route body (see api_server.py
    routes, none of which currently use Depends).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header.")
    raw_token = authorization.split(" ", 1)[1].strip()

    am = _make_auth_manager(conn)
    try:
        return am.get_principal(raw_token)
    except SessionExpired:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    except SessionInvalid:
        raise HTTPException(status_code=401, detail="Invalid session.")


def _require_permission(principal: Principal, permission: str) -> None:
    if not has_permission(principal.role, permission):
        raise HTTPException(
            status_code=403,
            detail=f"Role '{principal.role}' does not have permission '{permission}'.",
        )


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)
    station_id: Optional[str] = None


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str
    display_name: Optional[str] = None
    must_change_password: bool
    expires_at: float


class SessionResponse(BaseModel):
    user_id: int
    username: str
    role: str
    display_name: Optional[str] = None
    permissions: list[str]


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=256)
    role: str
    display_name: Optional[str] = None
    must_change_password: bool = True


class UpdateUserRequest(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=1, max_length=256)


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: Optional[str] = None
    role: str
    is_active: bool
    must_change_password: bool
    created_at: str
    created_by: Optional[str] = None
    last_login_at: Optional[str] = None


def _user_to_response(u) -> UserResponse:
    return UserResponse(
        id=u.id,
        username=u.username,
        display_name=u.display_name,
        role=u.role,
        is_active=u.is_active,
        must_change_password=u.must_change_password,
        created_at=u.created_at,
        created_by=u.created_by,
        last_login_at=u.last_login_at,
    )


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@auth_router.post("/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request):
    conn = _get_conn()
    try:
        am = _make_auth_manager(conn)
        try:
            result = am.login(
                payload.username,
                payload.password,
                ip_address=_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                station_id=payload.station_id,
            )
        except RateLimitedError as e:
            raise HTTPException(
                status_code=429, detail=str(e),
                headers={"Retry-After": str(e.retry_after_seconds)},
            )
        except AccountLockedError as e:
            raise HTTPException(
                status_code=423, detail=str(e),
                headers={"Retry-After": str(e.retry_after_seconds)},
            )
        except AccountDisabledError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except InvalidCredentialsError as e:
            raise HTTPException(status_code=401, detail=str(e))

        return LoginResponse(
            token=result.token,
            username=result.session.username,
            role=result.session.role,
            display_name=result.session.display_name,
            must_change_password=result.must_change_password,
            expires_at=result.session.expires_at,
        )
    finally:
        conn.close()


@auth_router.post("/auth/logout")
def logout(request: Request, authorization: Optional[str] = Header(None)):
    conn = _get_conn()
    try:
        if not authorization or not authorization.lower().startswith("bearer "):
            return {"status": "ok"}  # logout with no/garbled token is a no-op, not an error
        raw_token = authorization.split(" ", 1)[1].strip()
        am = _make_auth_manager(conn)
        am.logout(raw_token, ip_address=_client_ip(request))
        return {"status": "ok"}
    finally:
        conn.close()


@auth_router.get("/auth/session", response_model=SessionResponse)
def get_session(authorization: Optional[str] = Header(None)):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        granted = [p for p in ALL_PERMISSIONS if has_permission(principal.role, p)]
        return SessionResponse(
            user_id=principal.user_id,
            username=principal.username,
            role=principal.role,
            display_name=principal.display_name,
            permissions=granted,
        )
    finally:
        conn.close()


@auth_router.post("/auth/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        am = _make_auth_manager(conn)
        try:
            am.change_own_password(
                principal.user_id,
                payload.current_password,
                payload.new_password,
                ip_address=_client_ip(request),
            )
        except InvalidCredentialsError as e:
            raise HTTPException(status_code=401, detail=str(e))
        except PasswordPolicyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"status": "ok", "detail": "Password changed. Please log in again."}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User management routes (administrator only, enforced via PERM_USERS_MANAGE)
# ---------------------------------------------------------------------------

@auth_router.get("/admin/users", response_model=list[UserResponse])
def list_users(authorization: Optional[str] = Header(None)):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        am = _make_auth_manager(conn)
        return [_user_to_response(u) for u in am.users.list_users()]
    finally:
        conn.close()


@auth_router.post("/admin/users", response_model=UserResponse, status_code=201)
def create_user(
    payload: CreateUserRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        am = _make_auth_manager(conn)
        try:
            user = am.users.create_user(
                username=payload.username,
                password=payload.password,
                role=payload.role,
                display_name=payload.display_name,
                created_by=principal.username,
                must_change_password=payload.must_change_password,
                actor_role=principal.role,
            )
        except UsernameTakenError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except UnknownRoleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except InsufficientPrivilegeError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except PasswordPolicyError as e:
            raise HTTPException(status_code=400, detail=str(e))

        am.log_user_created(
            actor_username=principal.username,
            target_username=user.username,
            target_role=user.role,
            ip_address=_client_ip(request),
        )
        return _user_to_response(user)
    finally:
        conn.close()


@auth_router.put("/admin/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        am = _make_auth_manager(conn)
        try:
            user = am.users.update_user(
                user_id,
                display_name=payload.display_name,
                role=payload.role,
                actor_role=principal.role,
            )
        except UserNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except UnknownRoleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except InsufficientPrivilegeError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except LastAdministratorError as e:
            raise HTTPException(status_code=409, detail=str(e))

        if payload.role is not None:
            am.log_role_changed(
                actor_username=principal.username,
                target_user_id=user_id,
                new_role=payload.role,
                ip_address=_client_ip(request),
            )
        return _user_to_response(user)
    finally:
        conn.close()


@auth_router.delete("/admin/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        if user_id == principal.user_id:
            raise HTTPException(status_code=400, detail="You cannot delete your own account.")
        am = _make_auth_manager(conn)
        try:
            target = am.users.get_user(user_id)
            am.users.delete_user(user_id)
        except UserNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except LastAdministratorError as e:
            raise HTTPException(status_code=409, detail=str(e))

        am.log_user_deleted(
            actor_username=principal.username,
            target_username=target.username,
            ip_address=_client_ip(request),
        )
        return {"status": "ok"}
    finally:
        conn.close()


@auth_router.post("/admin/users/{user_id}/disable", response_model=UserResponse)
def disable_user(
    user_id: int,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        if user_id == principal.user_id:
            raise HTTPException(status_code=400, detail="You cannot disable your own account.")
        am = _make_auth_manager(conn)
        try:
            am.users.disable_user(user_id)
        except UserNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except LastAdministratorError as e:
            raise HTTPException(status_code=409, detail=str(e))

        am.sessions.revoke_all_sessions_for_user(user_id)
        am.log_account_disabled(
            actor_username=principal.username,
            target_user_id=user_id,
            ip_address=_client_ip(request),
        )
        return _user_to_response(am.users.get_user(user_id))
    finally:
        conn.close()


@auth_router.post("/admin/users/{user_id}/enable", response_model=UserResponse)
def enable_user(
    user_id: int,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        am = _make_auth_manager(conn)
        try:
            am.users.enable_user(user_id)
        except UserNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))

        am.log_account_enabled(
            actor_username=principal.username,
            target_user_id=user_id,
            ip_address=_client_ip(request),
        )
        return _user_to_response(am.users.get_user(user_id))
    finally:
        conn.close()


@auth_router.post("/admin/users/{user_id}/reset-password")
def reset_password(
    user_id: int,
    payload: ResetPasswordRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_USERS_MANAGE)
        am = _make_auth_manager(conn)
        try:
            am.admin_reset_password(
                actor_username=principal.username,
                target_user_id=user_id,
                new_password=payload.new_password,
                ip_address=_client_ip(request),
            )
        except UserNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except PasswordPolicyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"status": "ok", "detail": "Password reset. User must change it at next login."}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Role routes (read-only — roles are fixed per spec; no role CRUD endpoint
# is implemented since "implement three default roles" does not ask for
# dynamic role creation, only for those three to exist and be assignable)
# ---------------------------------------------------------------------------

class RoleResponse(BaseModel):
    name: str
    permissions: list[str]


@auth_router.get("/admin/roles", response_model=list[RoleResponse])
def list_roles(authorization: Optional[str] = Header(None)):
    conn = _get_conn()
    try:
        principal = _require_principal(authorization, conn)
        _require_permission(principal, PERM_ROLES_MANAGE)
        return [
            RoleResponse(name=role, permissions=list(ROLE_PERMISSIONS.get(role, ())))
            for role in DEFAULT_ROLES
        ]
    finally:
        conn.close()
