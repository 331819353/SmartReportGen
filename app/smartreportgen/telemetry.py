"""记录工具与内部阶段耗时；不记录 SQL、业务数据、工号或认证令牌。"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import json
import logging
import threading
from time import perf_counter
from uuid import uuid4

log = logging.getLogger("smartreportgen.timing")


@dataclass
class _TimingState:
    stages: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)
    active_stack: dict[int, list[str]] = field(default_factory=dict)
    failed_stage: str | None = None


_stages: ContextVar[_TimingState | None] = ContextVar("srg_timing_stages", default=None)


def failure_stage() -> str | None:
    state = _stages.get()
    return state.failed_stage if state else None


@contextmanager
def measure(stage: str):
    """汇总同名阶段；嵌套阶段可能重叠，不能把各阶段直接相加当作总耗时。"""
    state = _stages.get()
    if state is None:
        yield
        return
    started = perf_counter()
    thread_id = threading.get_ident()
    # 并行表画像和 Chromium 工作者共享计时汇总，各线程维护自己的嵌套阶段。
    with state.lock:
        state.active_stack.setdefault(thread_id, []).append(stage)
    try:
        yield
    except Exception:
        with state.lock:
            if state.failed_stage is None:
                state.failed_stage = state.active_stack[thread_id][-1]
        raise
    finally:
        elapsed = (perf_counter() - started) * 1000
        with state.lock:
            stack = state.active_stack[thread_id]
            stack.pop()
            if not stack:
                del state.active_stack[thread_id]
            item = state.stages.setdefault(stage, {"calls": 0, "elapsed_ms": 0.0})
            item["calls"] += 1
            item["elapsed_ms"] += elapsed


def observe_tool(fn):
    """为每次调用独立计时，字典响应附带阶段摘要。"""
    @wraps(fn)
    def observed(*args, **kwargs):
        state = _TimingState()
        token = _stages.set(state)
        started = perf_counter()
        request_id = uuid4().hex
        status = "error"
        result = None
        try:
            result = fn(*args, **kwargs)
            status = "error" if isinstance(result, dict) and result.get("ok") is False else "ok"
            return result
        finally:
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            with state.lock:
                stages = {name: {**item, "elapsed_ms": round(item["elapsed_ms"], 3)}
                          for name, item in state.stages.items()}
                failed_stage = state.failed_stage
            summary = {"request_id": request_id, "elapsed_ms": elapsed_ms, "stages": stages}
            # 先恢复上下文，避免并发请求或后续调用继承别人的阶段与计数。
            _stages.reset(token)
            if isinstance(result, dict):
                result["timing"] = summary
                if status == "error":
                    result.setdefault("failed_stage", failed_stage or "tool.execute")
                    result.setdefault("diagnostics", {})
                    failed_stage = result["failed_stage"]
            log.info("%s", json.dumps({"event": "tool_timing", "tool": fn.__name__,
                                       "status": status, "failed_stage": failed_stage if status == "error" else None,
                                       **summary}, ensure_ascii=False))
    return observed
