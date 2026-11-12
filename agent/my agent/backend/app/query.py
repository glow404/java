"""把受限结构化计划编译成参数化 SQL；模型不能提供物理表名或 SQL。"""

import math
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from sqlalchemy import select, cast, String, Numeric, BigInteger, Boolean, Date, func, case, and_
from fastapi import HTTPException
from . import db
from .config import settings
from .schemas import QueryPlan, FieldMeta, PlanAssumption


def value_for_type(value, type_name):
    if value is None or value == "":
        return None
    if type_name == "string":
        if isinstance(value, (dict, list)):
            raise ValueError("文本字段不能是对象或数组")
        return str(value)
    if type_name == "boolean":
        if value in (True, "true", "True", "是"):
            return True
        if value in (False, "false", "False", "否"):
            return False
        raise ValueError("布尔值应为 true/false 或 是/否")
    if type_name in ("number", "integer"):
        if isinstance(value, bool):
            raise ValueError("布尔值不能当作数值")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("不支持无限值或 NaN")
        if type_name == "integer":
            if not number.is_integer() or abs(number) > 9007199254740991:
                raise ValueError("整数超出精确范围或包含小数")
            return int(number)
        return number
    if type_name == "date":
        return date.fromisoformat(str(value)).isoformat()
    raise ValueError("未知字段类型")


def registry(table):
    if table.get("_relation_registry"):
        # 此标记仅由服务端已确认关联上下文构造；合成 ID 不会写回原表字段。
        return {f["id"]: dict(f) for f in table["fields"]}
    return {f["id"]: FieldMeta.model_validate(f).model_dump() for f in table["fields"]}


def proportional_unit(unit):
    """只识别明确的比例表示法；厘米、分等业务单位不参与自动换算。"""
    normalized = unicodedata.normalize("NFKC", str(unit or "")).strip().lower().replace(" ", "")
    if normalized in {"%", "百分数", "百分比", "percent", "percentage", "pct"}:
        return "percent"
    if normalized in {"ratio", "fraction", "decimal", "小数比例", "小数比率", "小数", "比例", "比率", "无量纲", "1"}:
        return "ratio"
    return None


def unit_assumption(plan, field, storage_unit):
    label = "小数比例（百分数需除以 100）" if storage_unit == "ratio" else "百分数（小数比例需乘以 100）"
    subject = f"服务器校验：{field['name']} 存储单位"
    # 缺失登记单位只能成为本次待确认的解释，不能暗中修改表结构；重复校验不重复追加。
    if not any(item.subject == subject for item in plan.assumptions):
        if len(plan.assumptions) >= 10:
            raise HTTPException(422, "本次口径判断项过多，请缩小问题范围后重新预览")
        plan.assumptions.append(PlanAssumption(subject=subject, choice=label,
            reason="字段登记单位为空；采用本次计划提出的存储表示法，确认仅用于本次查询，不修改字段资料。",
            alternatives=["改用百分数存储" if storage_unit == "ratio" else "改用小数比例存储", "先补充字段单位再查询"]))


def normalize_numeric_unit(plan, item, field, value_attribute, *, null_test=False):
    registered = field.get("unit", "")
    source_unit = item.unit
    registered_kind, source_kind = proportional_unit(registered), proportional_unit(source_unit)
    storage_kind = item.storage_unit
    numeric = field["type"] in ("number", "integer")
    mismatch = "占比阈值单位与登记字段单位不匹配" if value_attribute == "threshold" else "过滤值单位与登记字段单位不匹配"
    if registered:
        if storage_kind and (not numeric or registered_kind != storage_kind):
            raise HTTPException(422, mismatch + "：存储表示法与登记单位冲突")
        if source_unit not in (None, "", registered) and not (numeric and registered_kind and source_kind):
            raise HTTPException(422, mismatch)
        storage_kind = registered_kind if numeric else None
    elif storage_kind or source_kind:
        if not numeric:
            raise HTTPException(422, mismatch + "：比例表示法只能用于数值字段")
        # 源值已明确是小数比例时可提出同表示法候选；仅有 % 不能证明整列也是百分数存储。
        storage_kind = storage_kind or ("ratio" if source_kind == "ratio" else None)
        if not storage_kind:
            raise HTTPException(422, "字段登记单位为空，请确认按小数比例还是百分数存储；不能仅凭百分号判断")
        if source_unit not in (None, "") and not source_kind:
            raise HTTPException(422, mismatch)
        unit_assumption(plan, field, storage_kind)
    elif source_unit not in (None, ""):
        raise HTTPException(422, mismatch)

    if numeric and storage_kind and source_kind and source_kind != storage_kind and not null_test:
        factor = Decimal("0.01") if source_kind == "percent" else Decimal("100")
        value = getattr(item, value_attribute)
        try:
            # in 的每一个边界值都按同一登记单位换算；空值保持空，布尔值不得混入数值。
            def convert(raw):
                if raw is None:
                    return None
                if value_for_type(raw, "number") is None:
                    return None
                # 十进制换算后才转JSON数值，避免57%变成0.5700000000000001而误纳入严格小于范围。
                return value_for_type(str(Decimal(str(raw)) * factor), field["type"])
            converted = [convert(raw) for raw in value] if isinstance(value, list) else convert(value)
        except (ValueError, TypeError, OverflowError, InvalidOperation) as exc:
            raise HTTPException(422, f"比例单位换算值无效：{exc}") from exc
        setattr(item, value_attribute, converted)
    # value 已采用存储表示法。回写登记字符串后，预览/确认/重放不会再次除以 100。
    item.unit = registered if source_unit is not None or storage_kind else None
    item.storage_unit = storage_kind


def validate_plan(plan: QueryPlan, table):
    if table.get("archived"):
        raise HTTPException(409, "数据表已归档，请先恢复")
    if getattr(plan, "relation_id", None) and not table.get("_relation_registry"):
        from .relations import confirmed_context
        with db.engine.connect() as conn:
            context = confirmed_context(conn, plan.relation_id, table["owner_id"], plan.table_id)
        fields = validate_plan(plan, relation_table(context))
        validate_relation_statistics(plan, context)
        return fields
    fields = registry(table)
    if plan.operation == "metadata":
        return fields
    references = plan.fields + plan.group_by + [f.field_id for f in plan.filters] + list(plan.values)
    references += [a.field_id for a in plan.aggregates if a.field_id]
    if plan.ratio:
        references.append(plan.ratio.field_id)
    if plan.extremum:
        references.append(plan.extremum.field_id)
    aggregate_ids = {a.alias for a in plan.aggregates}
    if len(aggregate_ids) != len(plan.aggregates) or aggregate_ids.intersection(fields) or aggregate_ids.intersection({"ratio", "numerator", "denominator"}):
        raise HTTPException(422, "统计别名重复或与字段冲突")
    for s in plan.sort:
        ratio_ids = {"ratio", "numerator", "denominator"} if plan.ratio else set()
        if s.field_id not in aggregate_ids and s.field_id not in ratio_ids:
            references.append(s.field_id)
    if any(f not in fields for f in references):
        raise HTTPException(422, "计划包含不存在的字段，请核对字段标识")
    for a in plan.aggregates:
        if a.function != "count" and not a.field_id:
            raise HTTPException(422, "统计函数缺少字段")
        if a.function in ("sum", "avg") and fields[a.field_id]["type"] not in ("number", "integer"):
            raise HTTPException(422, "求和与均值只能使用数值字段")
        if a.function == "sum" and any(term in fields[a.field_id].get("description", "") for term in ("学生汇总", "一级指标汇总")):
            raise HTTPException(422, "汇总字段需确认唯一粒度，不能在明细表重复求和")
    if plan.ratio and fields[plan.ratio.field_id]["type"] not in ("number", "integer"):
        raise HTTPException(422, "占比的阈值字段必须是数值")
    if plan.ratio:
        normalize_numeric_unit(plan, plan.ratio, fields[plan.ratio.field_id], "threshold")
    if plan.extremum:
        if plan.operation != "select" or plan.group_by or plan.aggregates or plan.ratio:
            raise HTTPException(422, "对应极值记录查询只能用于独立数值查询，不能与分组或统计混用")
        if fields[plan.extremum.field_id]["type"] not in ("number", "integer"):
            raise HTTPException(422, "最优值对应字段必须为数值字段")
    if (plan.group_by or plan.aggregates or plan.ratio) and any(f not in plan.group_by for f in plan.fields):
        raise HTTPException(422, "统计结果只能选择分组字段与聚合字段")
    for f in plan.filters:
        kind = fields[f.field_id]["type"]
        normalize_numeric_unit(plan, f, fields[f.field_id], "value", null_test=f.op == "is_null")
        if f.op == "contains" and kind != "string":
            raise HTTPException(422, "包含查询只能用于文本字段")
        if f.op == "is_null":
            if f.value not in (None, True, False):
                raise HTTPException(422, "空值过滤必须使用布尔值")
            continue
        try:
            if f.op == "in":
                if not isinstance(f.value, list) or not f.value or len(f.value) > 100:
                    raise ValueError("in 条件须为 1 至 100 个值")
                f.value = [value_for_type(v, kind) for v in f.value]
            else:
                f.value = value_for_type(f.value, kind)
                if f.value is None and f.op not in ("eq", "ne"):
                    raise ValueError("此运算不接受空值")
        except (ValueError, TypeError, OverflowError) as exc:
            raise HTTPException(422, f"过滤值与字段类型不匹配：{exc}") from exc
    try:
        plan.values = {k: value_for_type(v, fields[k]["type"]) for k, v in plan.values.items()}
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(422, f"写入值与字段类型不匹配：{exc}") from exc
    return fields


def field_expr(field, rows=None, source_field_id=None):
    # JSON key 来自已登记的字段，而不是模型提供的数据库列名。
    raw = (rows if rows is not None else db.data_rows).c["values"][source_field_id or field["id"]].astext
    types = {"string": String, "number": Numeric, "integer": BigInteger, "boolean": Boolean, "date": Date}
    return cast(raw, types[field["type"]])


def filter_conditions(plan, fields, expression_for=field_expr):
    clauses = []
    for item in plan.filters:
        expr, value = expression_for(fields[item.field_id]), item.value
        ops = {"eq": lambda: expr == value, "ne": lambda: expr != value, "gt": lambda: expr > value,
               "gte": lambda: expr >= value, "lt": lambda: expr < value, "lte": lambda: expr <= value,
               "contains": lambda: expr.contains(value, autoescape=True), "in": lambda: expr.in_(value),
               "is_null": lambda: expr.is_not(None) if value is False else expr.is_(None)}
        clauses.append(ops[item.op]())
    return clauses


def conditions(plan, fields, owner_id):
    return and_(db.data_rows.c.owner_id == owner_id, db.data_rows.c.table_id == plan.table_id,
        *filter_conditions(plan, fields))


def set_timeout(conn):
    conn.execute(select(func.set_config("statement_timeout", str(settings.query_timeout_ms), True)))


def target_rows(conn, plan, table, owner_id):
    fields = validate_plan(plan, table)
    set_timeout(conn)
    rows = conn.execute(select(db.data_rows).where(conditions(plan, fields, owner_id))
        .order_by(db.data_rows.c.position, db.data_rows.c.id).limit(settings.mutation_limit + 1)).mappings().all()
    if len(rows) > settings.mutation_limit:
        raise HTTPException(422, f"影响范围超过 {settings.mutation_limit} 条，请缩小范围")
    return [{"id": r["id"], "values": r["values"], "source": r["source"]} for r in rows]


def relation_table(context):
    return {**context["left"], "fields": context["fields"], "_relation_registry": True}


def validate_relation_statistics(plan, context):
    # 一对多会复制一侧的值，不能把重复的学生汇总/金额当成真实总体重复统计。
    for aggregate in plan.aggregates:
        if aggregate.function in ("sum", "avg") and aggregate.field_id:
            side = aggregate.field_id.split("__", 1)[0]
            if context["stats"].get(f"{side}_multiplication"):
                raise HTTPException(422, "关联复制了该字段所在表的记录，不能直接求和或平均；请按实际粒度使用原表统计")
    if plan.ratio:
        side = plan.ratio.field_id.split("__", 1)[0]
        if context["stats"].get(f"{side}_multiplication"):
            raise HTTPException(422, "关联复制了阈值字段所在表的记录，不能直接计算占比；请使用原表明确分母")


def execute_query(conn, plan, table, owner_id):
    context = None
    expression_for = field_expr
    source = db.data_rows
    count_id = db.data_rows.c.id
    default_order = [db.data_rows.c.position, db.data_rows.c.id]
    scope = [db.data_rows.c.owner_id == owner_id, db.data_rows.c.table_id == plan.table_id]
    if getattr(plan, "relation_id", None):
        from .relations import confirmed_context, selected_field
        context = confirmed_context(conn, plan.relation_id, owner_id, plan.table_id, lock=True)
        table = relation_table(context)
        fields = validate_plan(plan, table)
        validate_relation_statistics(plan, context)
        left_rows, right_rows = db.data_rows.alias("relation_left"), db.data_rows.alias("relation_right")
        left_key = field_expr(selected_field(context["left"], context["relation"]["left_field_id"]), left_rows)
        right_key = field_expr(selected_field(context["right"], context["relation"]["right_field_id"]), right_rows)
        nonempty = [left_key.is_not(None), right_key.is_not(None)]
        if selected_field(context["left"], context["relation"]["left_field_id"])["type"] == "string":
            nonempty += [left_key != "", right_key != ""]
        # 右表权限放在 ON 内，left join 才能安全保留左表的未匹配记录。
        source = left_rows.join(right_rows, and_(left_key == right_key, right_rows.c.owner_id == owner_id,
            right_rows.c.table_id == context["right"]["id"], *nonempty), isouter=context["relation"]["join_type"] == "left")
        scope = [left_rows.c.owner_id == owner_id, left_rows.c.table_id == context["left"]["id"]]
        count_id = left_rows.c.id
        default_order = [left_rows.c.position, left_rows.c.id, right_rows.c.position, right_rows.c.id]

        def expression_for(field):
            side, original_id = field["id"].split("__", 1)
            return field_expr(field, left_rows if side == "left" else right_rows, original_id)
    else:
        fields = validate_plan(plan, table)
    set_timeout(conn)
    aggregate = bool(plan.aggregates or plan.group_by or plan.ratio)
    selected = plan.group_by if aggregate else (plan.fields or list(fields))
    columns = [fields[f] for f in selected]
    expressions = {f: expression_for(fields[f]).label(f) for f in selected}
    for a in plan.aggregates:
        expr = expression_for(fields[a.field_id]) if a.field_id else count_id
        expressions[a.alias] = getattr(func, a.function)(expr).label(a.alias)
        columns.append({"id": a.alias, "name": a.alias, "type": "number", "unit": "条" if a.function == "count" else fields[a.field_id].get("unit", "")})
    if plan.ratio:
        value = expression_for(fields[plan.ratio.field_id])
        numerator = func.sum(case((value >= plan.ratio.threshold, 1), else_=0))
        denominator = func.count(count_id) if plan.ratio.denominator == "all" else func.count(value)
        expressions.update(numerator=numerator.label("numerator"), denominator=denominator.label("denominator"),
            ratio=(100.0 * numerator / func.nullif(denominator, 0)).label("ratio"))
        columns.extend([{ "id": k, "name": label, "type": "number", "unit": unit} for k, label, unit in
            [("numerator", "达到阈值记录数", "条"), ("denominator", "分母记录数", "条"), ("ratio", "达标率", "%")]])
    scope_filters = [*scope, *filter_conditions(plan, fields, expression_for)]
    stmt = select(*expressions.values()).select_from(source).where(*scope_filters)
    if plan.extremum:
        metric = expression_for(fields[plan.extremum.field_id])
        # 在同一账号、表和过滤范围内求极值；保留所有并列记录，再进行稳定排序和分页。
        # correlate(None) 禁止 SQLAlchemy 把子查询的行源自动关联到外层单行。
        optimum = select(getattr(func, plan.extremum.direction)(metric)).select_from(source).where(*scope_filters).correlate(None).scalar_subquery()
        stmt = stmt.where(metric == optimum)
    if plan.group_by:
        # 复用 SELECT 表达式，也复用 JSON key 的绑定参数；PostgreSQL 不会把两个不同参数当成同一分组列。
        stmt = stmt.group_by(*(expressions[f] for f in plan.group_by))
    for order in plan.sort:
        if order.field_id not in expressions:
            if aggregate:
                raise HTTPException(422, "统计排序字段不在结果中")
            expr = expression_for(fields[order.field_id])
        else:
            expr = expressions[order.field_id]
        stmt = stmt.order_by(expr.asc() if order.direction == "asc" else expr.desc())
    if not plan.sort and not aggregate:
        stmt = stmt.order_by(*default_order)
    elif plan.sort and not aggregate:
        stmt = stmt.order_by(*default_order)
    stmt = stmt.limit(min(plan.limit, settings.result_limit)).offset(plan.offset)
    # 数字来自数据库实际执行，快照保留计划与绑定参数，供回复和工作台共同引用。
    rows = [dict(r) for r in conn.execute(stmt).mappings()]
    extremum_diagnostic = None
    if plan.extremum and not rows:
        # 复用极值查询的行源、账号/表和过滤范围，计数不受极值等值条件或分页影响。
        # count(metric) 只统计非空值，避免把“字段缺失”误解释为“没有符合条件的记录”。
        counts = conn.execute(select(func.count().label("matching_rows"),
            func.count(metric).label("non_null_metric_rows")).select_from(source).where(*scope_filters)).mappings().one()
        reason = ("no_matching_rows" if counts["matching_rows"] == 0 else
            "metric_missing" if counts["non_null_metric_rows"] == 0 else
            "empty_page" if plan.offset > 0 else "empty_result")
        extremum_diagnostic = {"reason": reason, "matching_rows": counts["matching_rows"],
            "non_null_metric_rows": counts["non_null_metric_rows"], "metric_field_id": plan.extremum.field_id}
    chart, explanation = build_chart(plan, columns, rows, fields)
    if plan.extremum:
        metric = fields[plan.extremum.field_id]
        direction = "最小值" if plan.extremum.direction == "min" else "最大值"
        explanation += f" 在当前账号所选表及过滤条件范围内求 {metric['name']} 的{direction}，排除该字段空值；返回所有并列最优记录及其对应字段，仍受分页限制。单位为{metric.get('unit') or '未指定'}。"
        if extremum_diagnostic:
            reason = extremum_diagnostic["reason"]
            matching, valid = extremum_diagnostic["matching_rows"], extremum_diagnostic["non_null_metric_rows"]
            if reason == "no_matching_rows":
                explanation += " 当前账号所选表中没有符合过滤条件的记录，请核对过滤值、单位和查询范围。"
            elif reason == "metric_missing":
                explanation += f" 过滤条件匹配 {matching} 条记录，但 {metric['name']} 全部为空，无法计算{direction}和对应阈值；空值不按 0 计，请补充有效数据后重新查询。"
            elif reason == "empty_page":
                explanation += f" 过滤范围有 {matching} 条记录，其中 {valid} 条 {metric['name']} 非空；本页没有最优记录，分页偏移为 {plan.offset}，请将偏移设为 0 后重新查询。"
            else:
                explanation += f" 过滤范围有有效 {metric['name']} 数据，但本次结果为空；请重新预览并执行以核对当前数据。"
    if context:
        stats = context["stats"]
        explanation += f" 已确认的 {context['relation']['join_type']} 关联：{context['left']['name']} 与 {context['right']['name']}；连接基数 {stats['cardinality']}，连接记录数 {stats['joined_rows']}。计数与分母表示连接记录，不等同于独立学生/订单数量；空身份不参与匹配。"
    compiled = stmt.compile(dialect=conn.dialect)
    snapshot = {"plan": plan.model_dump(), "table_version": table["version"],
        "field_registry": fields, "parameterized_sql": str(compiled), "bindings": compiled.params,
        "limit": min(plan.limit, settings.result_limit), "offset": plan.offset, "executed_at": db.utcnow().isoformat()}
    if extremum_diagnostic:
        snapshot["extremum_diagnostic"] = extremum_diagnostic
    if context:
        snapshot.update(relation_id=plan.relation_id, relation=context["relation"], join_stats=context["stats"],
            table_versions={context["left"]["id"]: context["left"]["version"], context["right"]["id"]: context["right"]["version"]})
    return columns, rows, chart, explanation, snapshot


def build_chart(plan, columns, rows, registry=None):
    description = f"实际查询返回 {len(rows)} 条结果；最多 {min(plan.limit, settings.result_limit)} 条，偏移 {plan.offset}。"
    if plan.group_by:
        names = [((registry or {}).get(f) or {}).get("name", f) for f in plan.group_by]
        description += " 按" + "、".join(names) + "分组；统计范围仅含当前账号所选表及过滤条件匹配记录。"
    for measure in plan.aggregates:
        metric = (registry or {}).get(measure.field_id, {})
        name = metric.get("name") or measure.field_id or "记录"
        if measure.function == "avg":
            description += f" {name}均值：分子为非空值之和，分母为非空记录数；空值不按零计，单位为{metric.get('unit') or '未指定'}。"
        elif measure.function == "count":
            description += f" {name}计数：" + ("只计该字段非空记录" if measure.field_id else "计所有匹配记录，包含字段空值") + "，单位为条。"
        else:
            description += f" {name}{ {'sum':'合计','min':'最小值','max':'最大值'}[measure.function]}仅使用非空值，单位为{metric.get('unit') or '未指定'}。"
    chart = None
    index = {c["id"]: c for c in columns}
    if plan.ratio:
        base = "当前过滤范围内全部记录（含空值）" if plan.ratio.denominator == "all" else "当前过滤范围内该字段非空记录"
        metric = (registry or {}).get(plan.ratio.field_id, {})
        threshold_unit = metric.get("unit") or plan.ratio.unit or "未指定单位"
        metric_name = metric.get("name") or plan.ratio.field_id
        description += f" 达标率：分子为 {metric_name} ≥ {plan.ratio.threshold}（{threshold_unit}）的记录数；分母为{base}；分子 ÷ 分母 × 100%，每组独立计算。比率单位：%。"
    if plan.chart.type != "none" and rows:
        x = plan.chart.x or (plan.group_by[0] if plan.group_by else columns[0]["id"])
        y = "ratio" if plan.ratio else plan.chart.y or next((c["id"] for c in columns if c["type"] in ("number", "integer") and c["id"] != x), None)
        if x not in index or y not in index or index[y]["type"] not in ("number", "integer") or x == y:
            description += " 结果缺少合适的分类与数值字段，保留表格。"
        else:
            kind = plan.chart.type
            measure = next((a for a in plan.aggregates if a.alias == y), None)
            if kind == "pie" and (plan.ratio or not measure or measure.function not in ("count", "sum") or not plan.group_by or any((r.get(y) or 0) < 0 for r in rows)):
                kind = "bar"
                description += " 各组比率或非构成数据不能组成饼图，改用柱状图。"
            if kind == "line" and index[x]["type"] not in ("date", "number", "integer"):
                kind = "bar"
                description += " 分类字段没有时间或数值顺序，改用柱状图。"
            chart = {"type": kind, "x": x, "y": y, "title": plan.chart.title, "unit": index[y].get("unit", ""),
                "numerator": f"{plan.ratio.field_id} ≥ {plan.ratio.threshold} 的记录数" if plan.ratio else "",
                "denominator": base if plan.ratio else ""}
            description += f" 图表数值：{index[y]['name']}；单位：{index[y].get('unit') or '未指定'}。"
            if kind == "pie":
                description += " 饼图仅表示本次返回分类的非负数量/合计构成，分母是返回分类数值之和；分页或截断结果不代表整张表。"
    return chart, description
