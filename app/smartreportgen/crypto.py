"""数据源密码 / 令牌加密。"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets

from cryptography.fernet import Fernet, InvalidToken

from . import db
from .config import settings

log = logging.getLogger(__name__)
_fernet: Fernet | None = None


def _key() -> bytes:
    raw = settings.secret_key
    if not raw:
        raw = db.get_setting("generated_secret_key") or ""
        if not raw:
            raw = secrets.token_urlsafe(32)
            db.set_setting("generated_secret_key", raw)
            log.warning("未配置 SECRET_KEY，已自动生成并保存在数据库中；生产环境请显式配置 SECRET_KEY")
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


def fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key())
    return _fernet


def encrypt(plain: str | None) -> str | None:
    if not plain:
        return None
    return fernet().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("数据源密钥无法解密：SECRET_KEY 可能已变更，请重新填写密码") from e
