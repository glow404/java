"""口径建议的有限依据：只读比例字段范围，优先用户明确补充，不修改登记资料。"""

import re
from sqlalchemy import select, func
from . import db
from .query import field_expr, set_timeout
from .schemas import PlanAssumption
from fastapi import HTTPException


def is_rate_field(field):
    names = [field["name"], *field.get("aliases", [])]
    tokens = {"far", "frr", "falseacceptancerate", "falserejectionrate", "概率", "比例", "占比", "达标率", "合格率", "误识率", "误拒率", "拒真率"}
    return any(re.sub(r"[\s_\-]", "", name).casefold() in tokens for name in names)


def add_rate_hints(conn, table, fields):
    candidates = [field for field in fields if field["type"] in ("number", "integer") and not field.get("unit") and is_rate_field(field)][:5]
    if not candidates:
        return
    set_timeout(conn)
    # 只做账号范围内的聚合，不读取整表或把原始数值发给模型；范围仅支持候选，不能证明原单位。
    expressions = [value for index, field in enumerate(candidates) for value in (
        func.min(field_expr(field)).label(f"min_{index}"), func.max(field_expr(field)).label(f"max_{index}"))]
    bounds = conn.execute(select(*expressions).where(db.data_rows.c.owner_id == table["owner_id"], db.data_rows.c.table_id == table["id"])).mappings().one()
    for index, field in enumerate(candidates):
        low, high = bounds[f"min_{index}"], bounds[f"max_{index}"]
        if low is None or high is None or low < 0 or high > 100:
            continue
        scale = "ratio" if high <= 1 else "percent"
        field["rate_storage_hint"] = {"candidate": scale, "basis":
            "登记单位为空，已保存非空值均在0至1范围内，仅支持小数比例候选，也可能是较小的百分数" if scale == "ratio" else
            "登记单位为空，已保存非空值在0至100内且包含大于1的值，仅支持百分数候选",
            "certainty": "candidate_only"}


def explicit_scale(field, content, history):
    # 只读用户话语；助手曾列出两种备选的问句不能被当作用户已经选择。
    texts = [content] + [item["content"] for item in reversed(history or []) if item["role"] == "user"]
    names = [field["name"], field["id"], *field.get("aliases", [])]
    for text in texts:
        topics, affirmations = [], []
        for name in names:
            topic = re.escape(name) + r"[^。；;\n]{0,30}?(?:存储单位|单位)"
            topics.extend(re.finditer(topic, text, re.IGNORECASE))
            affirmations.extend(re.finditer(topic + r"\s*(?:[:：=]|是|为)\s*(小数比例|小数比率|百分数|百分比数值)", text, re.IGNORECASE))
        if topics:
            # 同一句更正以最后一次明确表述为准；新否定/疑问不能被更早的肯定句覆盖。
            latest_topic = max(topics, key=lambda item: item.start())
            latest = max(affirmations, key=lambda item: item.start()) if affirmations else None
            if latest is None or latest.start() < latest_topic.start():
                return None
            suffix = re.split(r"[。；;\n]", text[latest.end():], maxsplit=1)[0]
            if re.search(r"吗|么|呢|还是|或者|不确定|也许|可能|未确认|待确认|[?？]", suffix):
                return None
            return "ratio" if latest[1].startswith("小数") else "percent"
    return None


def apply_storage_context(plan, tables, content, history=None):
    if plan.operation != "select" or plan.relation_id:
        return
    table = next((table for table in tables if table["id"] == plan.table_id), None)
    if not table:
        return
    fields = {field["id"]: field for field in table["fields"]}
    for condition in [*plan.filters, *([plan.ratio] if plan.ratio else [])]:
        field = fields.get(condition.field_id)
        if not field or field.get("unit") or not is_rate_field(field):
            continue
        explicit = explicit_scale(field, content, history)
        candidate = (field.get("rate_storage_hint") or {}).get("candidate")
        # 用户明确补充优先于先前候选；最终转换和兼容性仍交由查询校验器执行。
        if explicit or (condition.storage_unit is None and candidate):
            condition.storage_unit = explicit or candidate
            subject = f"服务器校验：{field['name']} 存储单位"
            # 存储表示法不绑定具体边界，实际筛选值由校验后保存的计划展示，避免误写成固定 3%。
            choice = "小数比例" if condition.storage_unit == "ratio" else "百分数"
            reason = "用户已明确补充存储表示法；本次查询采用该口径，字段登记资料保持不变。" if explicit else field["rate_storage_hint"]["basis"] + "；仅作为本次查询的待确认解释，字段资料保持不变。"
            # 后端补充的真实依据替换同主题的模型说明，避免把未经核实的解释伪装成数据事实。
            plan.assumptions = [item for item in plan.assumptions if item.subject != subject]
            if len(plan.assumptions) >= 10:
                raise HTTPException(422, "建议口径过多，请缩小本次查询范围")
            plan.assumptions.append(PlanAssumption(subject=subject, choice=choice, reason=reason,
                alternatives=["改用百分数存储" if condition.storage_unit == "ratio" else "改用小数比例存储", "先核对并补充字段单位"]))
