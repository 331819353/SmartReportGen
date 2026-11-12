"""连接器工厂（按数据源 id + 更新时间缓存）。"""
from __future__ import annotations

import threading

from .. import crypto
from .api import APIConnector
from .base import Connector, ConnectorError, ensure_readonly, profile
from .sql import SQLConnector

SUPPORTED_TYPES = ("mysql", "postgresql", "oracle", "starrocks", "api")
_cache: dict[int, tuple[str, Connector]] = {}
_lock = threading.Lock()


def get_connector(ds: dict) -> Connector:
    """ds 为 datasource 表行（含 secret 密文）。"""
    if ds["type"] not in SUPPORTED_TYPES:
        raise ConnectorError(f"不支持的数据源类型：{ds['type']}")
    if not ds.get("enabled", True):
        raise ConnectorError(f"数据源 {ds['name']} 已停用")
    stamp = str(ds.get("updated_at"))
    with _lock:
        hit = _cache.get(ds["id"]) if ds.get("id") else None
        if hit and hit[0] == stamp:
            return hit[1]
        secret = crypto.decrypt(ds.get("secret"))
        conn: Connector = APIConnector(ds, secret) if ds["type"] == "api" else SQLConnector(ds, secret)
        if ds.get("id"):
            if hit:
                hit[1].close()
            _cache[ds["id"]] = (stamp, conn)
        return conn


def drop_connector(ds_id: int) -> None:
    with _lock:
        hit = _cache.pop(ds_id, None)
    if hit:
        hit[1].close()


__all__ = ["get_connector", "drop_connector", "ConnectorError", "ensure_readonly", "profile", "SUPPORTED_TYPES"]
