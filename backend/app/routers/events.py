"""M27 · 事件总线路由（prefix=/events）：订阅 / 订阅列表 / 台账 / 发布。

全部端点需登录；成功响应统一 {"code":0,"data":{...}}。
publish 默认 deliver=False → 绝不发网络请求；订阅 token 只回显前缀。
"""
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.deps import get_caller
from app.models.caller import CallerContext
from app.services import events

router = APIRouter(prefix="/events", tags=["events"])


class SubscribeIn(BaseModel):
    url: str
    event_types: List[str] = Field(default_factory=list)


class PublishIn(BaseModel):
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    deliver: bool = False


@router.post("/subscribe")
def subscribe(body: SubscribeIn, caller: CallerContext = Depends(get_caller)):
    try:
        data = events.subscribe(body.url, body.event_types, caller)
    except ValueError as e:
        # 私网 URL / 空 event_types：业务错误用 code 表达，不伪装成成功
        return {"code": 1, "message": str(e)}
    return {"code": 0, "data": data}


@router.get("/subscriptions")
def list_subscriptions(caller: CallerContext = Depends(get_caller)):
    rows = events.list_subscriptions(caller)
    return {"code": 0, "data": {"subscriptions": rows, "count": len(rows)}}


@router.get("")
def list_events(caller: CallerContext = Depends(get_caller)):
    rows = events.list_events()
    return {"code": 0, "data": {"events": rows, "count": len(rows)}}


@router.post("/publish")
def publish(body: PublishIn, caller: CallerContext = Depends(get_caller)):
    try:
        data = events.publish(body.event_type, body.payload, caller, deliver=body.deliver)
    except ValueError as e:
        return {"code": 1, "message": str(e)}
    return {"code": 0, "data": data}
