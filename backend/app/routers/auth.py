"""auth 路由：SSO 登录回调（第18章身份入口，对接企业 IM）。"""
from fastapi import APIRouter

from app.core.security import create_dev_token
from app.models.schemas import SsoCallbackRequest, SsoCallbackResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/sso/callback", response_model=SsoCallbackResponse)
async def sso_callback(req: SsoCallbackRequest):
    # 骨架：演示用——由 provider/code 换身份。生产对接企微/钉钉/飞书 OAuth。
    # 默认演示账号：张三 / 销售部 / 普通员工
    token = create_dev_token(user_id="u_1024", dept_id="d_sales", role="employee")
    return {
        "code": 0,
        "data": {
            "token": token,
            "expires_in": 7200,
            "user": {"userId": "u_1024", "name": "张三", "deptId": "d_sales", "role": "employee"},
            "device_bound": True,
            "need_stepup": False,
        },
    }
