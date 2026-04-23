"""基础健康检查接口。"""

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("")
def health_check() -> dict[str, str]:
    """用于确认服务是否正常启动。"""

    return {"status": "ok"}
