"""演示数据：零售销售（PostgreSQL）、供应链库存（PostgreSQL）、客服工单（HTTP API），以及配套数据集与样式规则。
仅在 SEED_DEMO=true 且尚未初始化过时执行一次。"""
from __future__ import annotations

import datetime as dt
import logging
import os
import random
from urllib.parse import urlparse

import psycopg

from . import db, repo
from .config import settings

log = logging.getLogger(__name__)

REGIONS = {
    "华东": ["上海", "江苏", "浙江", "山东"],
    "华南": ["广东", "福建"],
    "华北": ["北京", "河北", "天津"],
    "华中": ["湖北", "湖南", "河南"],
    "西南": ["四川", "重庆", "云南"],
    "西北": ["陕西", "甘肃"],
}
REGION_W = {"华东": 0.31, "华南": 0.2, "华北": 0.17, "华中": 0.14, "西南": 0.12, "西北": 0.06}
# 2026 年相对 2025 年的增长系数（制造可分析的差异）
REGION_GROWTH = {"华东": 1.10, "华南": 1.14, "华北": 0.92, "华中": 1.06, "西南": 1.38, "西北": 1.02}
CHANNEL_W = {2025: {"门店": 0.40, "线上商城": 0.33, "直播电商": 0.12, "经销商": 0.15},
             2026: {"门店": 0.31, "线上商城": 0.34, "直播电商": 0.23, "经销商": 0.12}}
PRODUCTS = [  # 品类, 商品, 均价, 毛利率
    ("家电", "空调", 3200, 0.19), ("家电", "冰箱", 2900, 0.21), ("家电", "洗衣机", 2100, 0.23),
    ("数码", "手机", 3800, 0.11), ("数码", "平板", 2600, 0.14), ("数码", "耳机", 520, 0.36),
    ("家居", "床垫", 1900, 0.41), ("家居", "沙发", 4300, 0.34),
    ("食品", "零食礼盒", 160, 0.44), ("食品", "粮油", 95, 0.16),
]
PRODUCT_W = [0.09, 0.08, 0.08, 0.13, 0.07, 0.12, 0.07, 0.05, 0.17, 0.14]
SEASON = {1: 0.95, 2: 0.72, 3: 0.92, 4: 0.95, 5: 1.02, 6: 1.32, 7: 0.98, 8: 0.97, 9: 1.03, 10: 1.12, 11: 1.45, 12: 1.18}


def _pick(rng: random.Random, weights: dict):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _gen_sales(rng: random.Random):
    orders, customers = [], []
    seg_w = {"新客": 0.3, "老客": 0.45, "会员": 0.25}
    for cid in range(1, 6001):
        reg = _pick(rng, REGION_W)
        customers.append((cid, f"客户{cid:05d}", _pick(rng, seg_w), reg,
                          dt.date(2022, 1, 1) + dt.timedelta(days=rng.randint(0, 1700))))
    day = dt.date(2025, 1, 1)
    end = dt.date(2026, 10, 31)
    oid = 100000
    while day <= end:
        y = day.year
        n = int(46 * SEASON[day.month] * (1.0 if y == 2025 else 1.38) * (1.25 if day.weekday() >= 5 else 1.0))
        n += rng.randint(-5, 5)
        for _ in range(max(n, 1)):
            reg = _pick(rng, REGION_W)
            if y == 2026 and rng.random() > REGION_GROWTH[reg] / 1.4:
                continue
            prov = rng.choice(REGIONS[reg])
            ch = _pick(rng, CHANNEL_W[y])
            pi = rng.choices(range(len(PRODUCTS)), weights=PRODUCT_W)[0]
            cat, prod, price, margin = PRODUCTS[pi]
            qty = rng.choices([1, 2, 3, 4], weights=[0.68, 0.2, 0.08, 0.04])[0]
            unit = price * rng.uniform(0.86, 1.12) * (0.93 if ch == "直播电商" else 1.0)
            amount = round(unit * qty, 2)
            m = margin * rng.uniform(0.75, 1.2) * (0.82 if ch == "直播电商" else 1.0) * (1.06 if ch == "门店" else 1.0)
            cost = round(amount * (1 - m), 2)
            cid = rng.randint(1, 6000)
            oid += 1
            orders.append((oid, day, reg, prov, ch, cat, prod, cid, qty, amount, cost, round(amount - cost, 2)))
        day += dt.timedelta(days=1)
    return orders, customers


def _gen_inventory(rng: random.Random):
    rows = []
    whs = {"上海仓": "华东", "广州仓": "华南", "北京仓": "华北", "成都仓": "西南"}
    d = dt.date(2025, 1, 6)
    while d <= dt.date(2026, 10, 26):
        for wh, reg in whs.items():
            for cat, prod, price, _m in PRODUCTS:
                base = int(4000 / (price ** 0.5))
                safety = int(base * 0.6)
                stock = max(0, int(base * rng.uniform(0.35, 1.6) * (0.8 if reg == "西南" and d.year == 2026 else 1)))
                inbound = int(base * rng.uniform(0.2, 0.6))
                outbound = int(base * rng.uniform(0.2, 0.62) * SEASON[d.month])
                rows.append((d, wh, reg, cat, prod, stock, safety, inbound, outbound, round(stock * price * 0.7, 2)))
        d += dt.timedelta(days=7)
    return rows


def demo_tickets() -> list[dict]:
    rng = random.Random(7)
    out = []
    cats = {"物流配送": 0.32, "售后退换": 0.24, "商品质量": 0.18, "支付问题": 0.11, "账户问题": 0.08, "活动咨询": 0.07}
    chans = {"在线客服": 0.42, "电话": 0.28, "App反馈": 0.2, "邮件": 0.1}
    for i in range(1, 3201):
        day = dt.date(2026, 1, 1) + dt.timedelta(days=rng.randint(0, 303))
        cat = _pick(rng, cats)
        ch = _pick(rng, chans)
        pri = rng.choices(["高", "中", "低"], weights=[0.18, 0.5, 0.32])[0]
        hours = round(rng.lognormvariate(2.2 if cat != "物流配送" else 2.7, 0.6) * (0.6 if pri == "高" else 1), 1)
        sat = max(1, min(5, round(5.2 - hours / 18 + rng.uniform(-1.2, 0.8))))
        status = rng.choices(["已解决", "已关闭", "处理中"], weights=[0.72, 0.18, 0.1])[0]
        out.append({
            "ticket_id": f"T{2026}{i:05d}", "created_date": day.isoformat(), "month": day.strftime("%Y-%m"),
            "channel": ch, "category": cat, "priority": pri, "status": status,
            "region": _pick(rng, REGION_W), "resolve_hours": hours if status != "处理中" else None,
            "satisfaction": sat if status != "处理中" else None,
        })
    return out


def _create_demo_tables(reader_pwd: str) -> None:
    rng = random.Random(42)
    orders, customers = _gen_sales(rng)
    inventory = _gen_inventory(rng)
    with db.conn() as c, c.transaction():
        c.execute("DROP SCHEMA IF EXISTS demo_retail CASCADE")
        c.execute("DROP SCHEMA IF EXISTS demo_supply CASCADE")
        c.execute("CREATE SCHEMA demo_retail")
        c.execute("CREATE SCHEMA demo_supply")
        c.execute("""CREATE TABLE demo_retail.sales_order(
            order_id bigint PRIMARY KEY, order_date date NOT NULL, region text, province text, channel text,
            category text, product text, customer_id int, quantity int, amount numeric(14,2), cost numeric(14,2),
            profit numeric(14,2))""")
        c.execute("""CREATE TABLE demo_retail.customer(customer_id int PRIMARY KEY, customer_name text, segment text,
            region text, register_date date)""")
        c.execute("""CREATE TABLE demo_retail.sales_target(month text, region text, target_amount numeric(16,2),
            PRIMARY KEY(month, region))""")
        c.execute("""CREATE TABLE demo_supply.inventory_snapshot(snapshot_date date, warehouse text, region text,
            category text, product text, stock_qty int, safety_stock int, inbound_qty int, outbound_qty int,
            stock_value numeric(16,2))""")
        comments = {
            "demo_retail.sales_order": ("零售订单明细，一行一个订单", {
                "order_id": "订单号", "order_date": "下单日期", "region": "大区", "province": "省份", "channel": "销售渠道",
                "category": "商品品类", "product": "商品", "customer_id": "客户ID", "quantity": "件数",
                "amount": "销售额(元)", "cost": "成本(元)", "profit": "毛利(元)"}),
            "demo_retail.customer": ("客户档案", {"segment": "客户分层：新客/老客/会员", "register_date": "注册日期"}),
            "demo_retail.sales_target": ("大区月度销售目标", {"month": "月份 YYYY-MM", "target_amount": "目标销售额(元)"}),
            "demo_supply.inventory_snapshot": ("仓库商品周度库存快照", {"stock_qty": "库存数量", "safety_stock": "安全库存",
                                                                  "inbound_qty": "本周入库", "outbound_qty": "本周出库",
                                                                  "stock_value": "库存金额(元)"}),
        }
        for t, (tc, cols) in comments.items():
            c.execute(f"COMMENT ON TABLE {t} IS '{tc}'")
            for col, cc in cols.items():
                c.execute(f"COMMENT ON COLUMN {t}.{col} IS '{cc}'")
        with c.cursor().copy("COPY demo_retail.sales_order FROM STDIN") as cp:
            for r in orders:
                cp.write_row(r)
        with c.cursor().copy("COPY demo_retail.customer FROM STDIN") as cp:
            for r in customers:
                cp.write_row(r)
        with c.cursor().copy("COPY demo_supply.inventory_snapshot FROM STDIN") as cp:
            for r in inventory:
                cp.write_row(r)
        c.execute("""INSERT INTO demo_retail.sales_target
            SELECT to_char(order_date,'YYYY-MM'), region,
                   round(sum(amount) * (0.92 + (abs(hashtext(to_char(order_date,'YYYY-MM')||region)) % 20) / 100.0), -3)
            FROM demo_retail.sales_order GROUP BY 1, 2""")
        exists = c.execute("SELECT 1 FROM pg_roles WHERE rolname='demo_reader'").fetchone()
        pwd = reader_pwd.replace("'", "''")
        c.execute(f"{'ALTER' if exists else 'CREATE'} ROLE demo_reader LOGIN PASSWORD '{pwd}'")
        dbname = c.execute("SELECT current_database() AS d").fetchone()["d"]
        c.execute(f'GRANT CONNECT ON DATABASE "{dbname}" TO demo_reader')
        for s in ("demo_retail", "demo_supply"):
            c.execute(f"GRANT USAGE ON SCHEMA {s} TO demo_reader")
            c.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO demo_reader")
    log.info("演示数据：订单 %s 行，客户 %s 行，库存快照 %s 行", len(orders), len(customers), len(inventory))


def seed_if_needed() -> None:
    if not settings.seed_demo or db.get_setting("demo_seeded"):
        return
    with db.conn() as c:
        c.execute("SELECT pg_advisory_lock(4242002)")
        try:
            if db.get_setting("demo_seeded"):
                return
            _seed()
            db.set_setting("demo_seeded", dt.datetime.now().isoformat())
        finally:
            c.execute("SELECT pg_advisory_unlock(4242002)")


def _seed() -> None:
    reader_pwd = os.getenv("DEMO_READER_PASSWORD", "demo_reader_pwd")
    _create_demo_tables(reader_pwd)
    u = urlparse(settings.database_url.replace("postgresql+psycopg", "postgresql"))
    pg = repo.create_datasource({
        "name": "演示-零售PG", "type": "postgresql", "secret": reader_pwd,
        "config": {"host": u.hostname, "port": u.port or 5432, "database": (u.path or "/").lstrip("/"),
                   "username": "demo_reader"},
        "description": "演示用 PostgreSQL（只读账号 demo_reader），含 demo_retail / demo_supply 两个 schema",
    })
    api = repo.create_datasource({
        "name": "演示-客服工单API", "type": "api",
        "config": {"base_url": settings.demo_api_base_url, "records_path": "data.items", "cache_seconds": 60,
                   "endpoints": {"tickets": {"path": "/tickets", "method": "GET",
                                             "description": "客服工单列表（2026年）"}}},
        "description": "演示用 HTTP API 数据源（由本服务 /demo-api 提供），SQL 使用 DuckDB 方言",
    })
    repo.create_dataset({
        "business_scenario": "零售销售经营分析", "datasource_id": pg["id"],
        "tags": ["销售", "零售", "经营", "毛利", "渠道", "区域", "GMV"],
        "business_description": "覆盖全国六大区、17 个省份的零售订单数据（2025-01 至 2026-10），包含销售额、成本、毛利、"
                                "订单量、客户等信息。适用于销售业绩复盘、增长趋势、区域与省份对比、渠道结构变化（门店/线上商城/"
                                "直播电商/经销商）、品类与商品贡献、毛利率分析、客户分层与销售目标达成分析。",
        "tables": [
            {"table_name": "demo_retail.sales_order", "table_description": "零售订单明细：下单日期、大区、省份、渠道、品类、商品、客户、件数、销售额、成本、毛利"},
            {"table_name": "demo_retail.customer", "table_description": "客户档案：客户分层（新客/老客/会员）、所属大区、注册日期"},
            {"table_name": "demo_retail.sales_target", "table_description": "大区月度销售目标（month 为 YYYY-MM）"},
        ],
    })
    repo.create_dataset({
        "business_scenario": "供应链库存管理", "datasource_id": pg["id"], "tags": ["库存", "仓储", "供应链", "周转"],
        "business_description": "四大区域仓库的商品周度库存快照，包含库存数量、安全库存、出入库数量与库存金额，"
                                "用于库存健康度、缺货风险、库存周转与仓库对比分析。",
        "tables": [{"table_name": "demo_supply.inventory_snapshot", "table_description": "仓库商品周度库存快照"}],
    })
    repo.create_dataset({
        "business_scenario": "客户服务工单分析", "datasource_id": api["id"], "tags": ["客服", "工单", "满意度", "投诉"],
        "business_description": "2026 年客户服务工单数据（来自客服系统 API），包含受理渠道、问题分类、优先级、处理状态、"
                                "处理时长与满意度评分，用于服务质量、问题热点、处理时效与满意度分析。",
        "tables": [{"table_name": "tickets", "table_description": "客服工单：ticket_id、created_date、month、channel、category、priority、status、region、resolve_hours、satisfaction"}],
    })
    log.info("演示数据源、数据集已创建")


# ------------------------------------------------------------ 版本化迁移（按标记只执行一次）
# 历史迁移（预置样式规则、演示员工）已移除：系统不再预置任何样式规则与测试用户，
# 样式由管理员在管理台配置，未配置的项使用内置默认（design.py）。保留空迁移键，已执行过的库不受影响。
def _noop() -> None:
    pass


MIGRATIONS = [("mig_style_v2", _noop), ("mig_perm_demo_v1", _noop), ("mig_style_v3", _noop)]


def migrate() -> None:
    with db.conn() as c:
        c.execute("SELECT pg_advisory_lock(4242003)")
        try:
            for key, fn in MIGRATIONS:
                if not db.get_setting(key):
                    fn()
                    db.set_setting(key, dt.datetime.now().isoformat())
                    log.info("迁移完成：%s", key)
        finally:
            c.execute("SELECT pg_advisory_unlock(4242003)")
