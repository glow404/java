"""把名称或别名映射到已授权字段；同义词歧义返回澄清，绝不生成物理列名。"""
from copy import deepcopy
import re
import unicodedata
from fastapi import HTTPException
from .schemas import QueryPlan

SYNONYMS = (
    {"成绩", "分数", "得分"}, {"学号", "学生编号", "学生号", "学生id"},
    {"订单号", "订单编号", "订单id"}, {"班级", "班别", "班组"},
)


def normalized(value):
    return re.sub(r"[\s_\-]", "", unicodedata.normalize("NFKC", value).casefold())


def labels(field):
    names = {field["name"], *field.get("aliases", [])}
    equivalents = {normalized(name) for name in names}
    for group in SYNONYMS:
        if equivalents.intersection(group):
            names.update(group)
    return names


def field_id(reference, fields):
    if any(field["id"] == reference for field in fields):
        return reference
    candidates = [field for field in fields if normalized(reference) in {normalized(name) for name in labels(field)}]
    if len(candidates) == 1:
        return candidates[0]["id"]
    if candidates:
        raise HTTPException(422, f"“{reference}”对应多个字段：{'、'.join(field['name'] for field in candidates)}。请明确表名及字段。")
    raise HTTPException(422, f"未找到“{reference}”对应的登记字段，请补充字段说明或别名。")


def enrich_tables(tables):
    enriched = deepcopy(tables)
    for table in enriched:
        for field in table["fields"]:
            field["aliases"] = sorted(labels(field) - {field["name"]})
    return enriched


def resolve_plan(plan: QueryPlan, tables):
    if plan.operation == "clarify":
        return plan
    selected = [table for table in tables if table["id"] == plan.table_id]
    if not selected:
        selected = [table for table in tables if normalized(table["name"]) == normalized(plan.table_id or "")]
    if len(selected) != 1:
        raise HTTPException(422, "请明确当前选择范围中的唯一数据表。")
    table = selected[0]
    plan.table_id = table["id"]
    fields = table["fields"]
    if plan.relation_id:
        relation = next((item for item in table.get("confirmed_relations", []) if item["id"] == plan.relation_id), None)
        if relation is None:
            raise HTTPException(422, "请先在当前选择范围中确认两表关联，再使用该关联查询。")
        fields = relation["fields"]
    # 仅在注册表内解析；别名不能扩大当前账号或本次选择的数据范围。
    plan.fields = [field_id(reference, fields) for reference in plan.fields]
    plan.group_by = [field_id(reference, fields) for reference in plan.group_by]
    for condition in plan.filters:
        condition.field_id = field_id(condition.field_id, fields)
    for aggregate in plan.aggregates:
        if aggregate.field_id:
            aggregate.field_id = field_id(aggregate.field_id, fields)
    aliases = {aggregate.alias for aggregate in plan.aggregates} | ({"ratio", "numerator", "denominator"} if plan.ratio else set())
    for order in plan.sort:
        if order.field_id not in aliases:
            order.field_id = field_id(order.field_id, fields)
    if plan.ratio:
        plan.ratio.field_id = field_id(plan.ratio.field_id, fields)
    if plan.extremum:
        plan.extremum.field_id = field_id(plan.extremum.field_id, fields)
    mapped = {}
    for reference, value in plan.values.items():
        identifier = field_id(reference, fields)
        if identifier in mapped:
            raise HTTPException(422, "多个别名引用同一写入字段，请合并修改值。")
        mapped[identifier] = value
    plan.values = mapped
    if plan.metadata:
        for change in plan.metadata.field_changes:
            change.field_id = field_id(change.field_id, fields)
    for axis in ("x", "y"):
        reference = getattr(plan.chart, axis)
        if reference and reference not in aliases:
            setattr(plan.chart, axis, field_id(reference, fields))
    return QueryPlan.model_validate(plan.model_dump())


def canonical_question(content, tables):
    """自动化替身也接受已登记别名；真实服务接收包含别名的元信息。"""
    replacements = {}
    for table in tables:
        for field in table["fields"]:
            for label in labels(field):
                replacements.setdefault(label, set()).add(field["name"])
    for alias in sorted(replacements, key=len, reverse=True):
        if len(replacements[alias]) == 1:
            content = content.replace(alias, next(iter(replacements[alias])))
    return content
