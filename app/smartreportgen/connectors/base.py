"""连接器公共能力：只读校验、结果归一化、数据画像。"""
from __future__ import annotations

import datetime as dt
import decimal
import re
import uuid
from collections import Counter
from typing import Any


class ConnectorError(RuntimeError):
    pass


_COMMENT_LINE = re.compile(r"--[^\n]*")
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|upsert|drop|alter|create|truncate|grant|revoke|call|exec|execute|"
    r"copy|vacuum|analyze|lock|unlock|kill|shutdown|into|outfile|dumpfile|attach|detach|pragma|install|load)\b",
    re.I,
)
IDENT_RE = re.compile(r'^[\w$#".]+$', re.U)


def ensure_readonly(sql: str) -> str:
    """只允许单条 SELECT / WITH 查询。数据源账号仍应配置为只读，这里是第二道防线。"""
    if not sql or not sql.strip():
        raise ConnectorError("SQL 为空")
    s = _COMMENT_BLOCK.sub(" ", sql)
    s = _COMMENT_LINE.sub(" ", s).strip()
    while s.endswith(";"):
        s = s[:-1].rstrip()
    bare = _STRING.sub("''", s)
    if ";" in bare:
        raise ConnectorError("只允许单条查询语句")
    if not re.match(r"(?is)^\s*\(?\s*(select|with)\b", bare):
        raise ConnectorError("只允许 SELECT / WITH 查询")
    m = _FORBIDDEN.search(bare)
    if m:
        raise ConnectorError(f"查询中包含不允许的关键字：{m.group(1)}")
    return s


def check_ident(name: str) -> str:
    if not name or not IDENT_RE.match(name):
        raise ConnectorError(f"非法表名：{name!r}")
    return name


def norm_value(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return None if v != v else v  # NaN -> None
    if isinstance(v, decimal.Decimal):
        f = float(v)
        return int(f) if v == v.to_integral_value() and abs(f) < 2**53 else f
    if isinstance(v, dt.datetime):
        if v.hour == v.minute == v.second == 0 and v.microsecond == 0:
            return v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, dt.time):
        return v.strftime("%H:%M:%S")
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return f"<binary {len(bytes(v))} bytes>"
    if hasattr(v, "read"):  # LOB
        try:
            return v.read()
        except Exception:  # noqa: BLE001
            return str(v)
    return str(v)


def norm_columns(cols: list[str]) -> list[str]:
    """列名统一小写，重名自动加后缀。"""
    out, seen = [], Counter()
    for c in cols:
        c = str(c).strip().strip('"').lower()
        seen[c] += 1
        out.append(c if seen[c] == 1 else f"{c}_{seen[c]}")
    return out


def _kind(values: list[Any]) -> str:
    nn = [v for v in values if v is not None]
    if not nn:
        return "unknown"
    if all(isinstance(v, bool) for v in nn):
        return "boolean"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nn):
        return "number"
    if all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2})?)?$", v) for v in nn):
        return "datetime"
    return "text"


def profile(columns: list[str], rows: list[list[Any]]) -> list[dict]:
    """基于样本行计算列画像：类型、空值率、去重数、最值、高频值。"""
    out = []
    n = len(rows)
    for i, c in enumerate(columns):
        vals = [r[i] for r in rows]
        nn = [v for v in vals if v is not None and v != ""]
        kind = _kind(vals)
        p: dict[str, Any] = {
            "column": c,
            "kind": kind,
            "null_ratio": round(1 - len(nn) / n, 4) if n else None,
            "distinct": len(set(map(str, nn))),
        }
        if nn and kind in ("number", "datetime"):
            p["min"], p["max"] = min(nn), max(nn)
            if kind == "number":
                p["avg"] = round(sum(nn) / len(nn), 4)
        if nn and kind in ("text", "boolean") or (kind == "number" and p["distinct"] <= 12):
            p["top_values"] = [
                {"value": v, "count": k} for v, k in Counter(map(str, nn)).most_common(10)
            ]
        out.append(p)
    return out


class Connector:
    type = "base"

    def __init__(self, ds: dict, secret: str):
        self.ds = ds
        self.cfg = ds.get("config") or {}
        self.secret = secret

    def test(self) -> dict:
        raise NotImplementedError

    def list_tables(self, schema: str | None = None) -> list[dict]:
        raise NotImplementedError

    def columns(self, table: str) -> list[dict]:
        raise NotImplementedError

    def query(self, sql: str, limit: int) -> dict:
        """返回 {columns, rows, truncated}"""
        raise NotImplementedError

    def sample(self, table: str, n: int) -> dict:
        raise NotImplementedError

    def count(self, table: str) -> int | None:
        return None

    def close(self) -> None:
        pass
