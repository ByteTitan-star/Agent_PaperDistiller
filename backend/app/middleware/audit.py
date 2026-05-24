import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("audit")

# 需要审计的路径前缀和动作映射
AUDIT_PATHS = {
    "POST:/api/auth/register": ("register", "user"),
    "POST:/api/auth/login": ("login", "user"),
    "POST:/api/upload": ("upload", "paper"),
    "DELETE:/api/papers": ("delete_paper", "paper"),
    "PUT:/api/settings/api-keys": ("config_change", "config"),
    "PUT:/api/admin/settings": ("config_change", "config"),
    "PUT:/api/auth/users": ("role_change", "user"),
}


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        method = request.method
        path = request.url.path

        # 检查是否需要审计
        audit_key = None
        for key_prefix, (action, resource_type) in AUDIT_PATHS.items():
            parts = key_prefix.split(":", 1)
            if len(parts) == 2 and parts[0] == method and path.startswith(parts[1]):
                audit_key = (action, resource_type)
                break

        if audit_key:
            action, resource_type = audit_key
            try:
                from ..database import async_session_factory
                from ..models import AuditLog

                user_id = None
                auth_header = request.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    from ..auth.jwt_utils import decode_access_token
                    payload = decode_access_token(auth_header[7:])
                    if payload:
                        user_id = int(payload.get("sub", 0))

                # Extract resource_id from path
                resource_id = None
                path_parts = path.rstrip("/").split("/")
                if len(path_parts) > 1:
                    resource_id = path_parts[-1]

                async with async_session_factory() as session:
                    log_entry = AuditLog(
                        user_id=user_id,
                        action=action,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        ip_address=request.client.host if request.client else None,
                        detail={"method": method, "path": path, "status_code": response.status_code},
                    )
                    session.add(log_entry)
                    await session.commit()
            except Exception as exc:
                logger.warning("Audit log write failed: %s", exc)

        return response
