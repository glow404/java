"""API 与模型计划的严格校验；未知字段不得扩大执行权限。"""

from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Strict):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=500)


class ProfilePatch(Strict):
    display_name: str = Field(default="", max_length=100)
    bio: str = Field(default="", max_length=2000)
    email: str | None = Field(default=None, max_length=254)
    current_password: str | None = Field(default=None, min_length=1, max_length=500)


class PasswordChange(Strict):
    current_password: str = Field(min_length=1, max_length=500)
    new_password: str = Field(min_length=12, max_length=500)


class FieldMeta(Strict):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
    name: str = Field(min_length=1, max_length=200)
    type: Literal["string", "number", "integer", "boolean", "date"] = "string"
    unit: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=2000)
    provenance: Literal["file", "user", "confirmed_inference"] = "file"
    type_provenance: Literal["file", "inference", "user", "confirmed_inference"] = "file"
    aliases: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_aliases(self):
        self.aliases = list(dict.fromkeys(alias.strip() for alias in self.aliases))
        if any(not alias for alias in self.aliases):
            raise ValueError("字段别名不能为空白")
        return self


class TablePatch(Strict):
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    description_provenance: Literal["file", "user", "confirmed_inference"] | None = None
    fields: list[FieldMeta] = Field(min_length=1, max_length=200)


class Correction(Strict):
    row_index: int = Field(ge=0)
    field_id: str
    value: Any


class ImportTable(Strict):
    candidate_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    description_provenance: Literal["file", "user", "confirmed_inference"] | None = None
    reviewed: bool
    fields: list[FieldMeta] = Field(min_length=1, max_length=200)
    corrections: list[Correction] = Field(default_factory=list, max_length=10000)


class ImportConfirm(Strict):
    version: int = Field(ge=1)
    tables: list[ImportTable] = Field(min_length=1, max_length=100)


class ConversationCreate(Strict):
    title: str = Field(default="新对话", min_length=1, max_length=200)


class TableCreate(Strict):
    conversation_id: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    fields: list[FieldMeta] = Field(min_length=1, max_length=200)


class TableState(Strict):
    conversation_id: str
    version: int = Field(ge=1)
    archived: bool


class RelationCreate(Strict):
    conversation_id: str
    left_table_id: str
    right_table_id: str
    left_field_id: str
    right_field_id: str
    join_type: Literal["inner", "left"] = "inner"


class RelationState(Strict):
    conversation_id: str
    status: Literal["inactive"] = "inactive"


class FieldChange(Strict):
    field_id: str
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=100)
    type: Literal["string", "number", "integer", "boolean", "date"] | None = None
    aliases: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=20)


class MetadataChange(Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    field_changes: list[FieldChange] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def has_changes(self):
        if self.name is None and self.description is None and not self.field_changes:
            raise ValueError("元数据计划必须包含修改项")
        if len({item.field_id for item in self.field_changes}) != len(self.field_changes):
            raise ValueError("字段修改不能重复")
        if any(not item.model_dump(exclude_none=True).keys() - {"field_id"} for item in self.field_changes):
            raise ValueError("字段修改必须包含修改值")
        return self


class ChatInput(Strict):
    content: str = Field(min_length=1, max_length=10000)
    table_ids: list[str] = Field(min_length=1, max_length=20)
    plan: "QueryPlan | None" = None


class Filter(Strict):
    field_id: str
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "is_null"]
    value: Any = None
    unit: str | None = None
    storage_unit: Literal["ratio", "percent"] | None = None


class Sort(Strict):
    field_id: str
    direction: Literal["asc", "desc"] = "asc"


class Aggregate(Strict):
    field_id: str | None = None
    function: Literal["count", "sum", "avg", "min", "max"]
    alias: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")


class Ratio(Strict):
    field_id: str
    threshold: float = Field(allow_inf_nan=False)
    denominator: Literal["all", "non_null"]
    unit: str | None = None
    storage_unit: Literal["ratio", "percent"] | None = None


class PlanAssumption(Strict):
    """仅用于待确认口径，不赋予权限或改写字段元数据。"""
    subject: str = Field(min_length=1, max_length=200)
    choice: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=1000)
    alternatives: list[str] = Field(default_factory=list, max_length=10)


class Extremum(Strict):
    field_id: str
    direction: Literal["min", "max"]


class Chart(Strict):
    type: Literal["bar", "line", "pie", "none"] = "none"
    x: str | None = None
    y: str | None = None
    title: str = Field(default="查询结果", max_length=200)


class QueryPlan(Strict):
    operation: Literal["select", "insert", "update", "delete", "metadata", "clarify"]
    table_id: str | None = None
    relation_id: str | None = None
    metadata: MetadataChange | None = None
    fields: list[str] = Field(default_factory=list, max_length=200)
    filters: list[Filter] = Field(default_factory=list, max_length=30)
    sort: list[Sort] = Field(default_factory=list, max_length=10)
    group_by: list[str] = Field(default_factory=list, max_length=10)
    aggregates: list[Aggregate] = Field(default_factory=list, max_length=20)
    ratio: Ratio | None = None
    extremum: Extremum | None = None
    assumptions: list[PlanAssumption] = Field(default_factory=list, max_length=10)
    limit: int = Field(default=100, ge=1, le=5000)
    offset: int = Field(default=0, ge=0, le=1000000)
    values: dict[str, Any] = Field(default_factory=dict)
    clarifications: list[str] = Field(default_factory=list, max_length=10)
    chart: Chart = Field(default_factory=Chart)

    @model_validator(mode="after")
    def coherent(self):
        if self.operation == "clarify":
            if not self.clarifications:
                raise ValueError("澄清计划必须包含问题")
            return self
        if not self.table_id:
            raise ValueError("执行计划必须包含逻辑表标识")
        if self.operation in ("insert", "update") and not self.values:
            raise ValueError("写入计划必须包含字段值")
        if self.operation in ("update", "delete") and not self.filters:
            raise ValueError("修改和删除必须明确过滤范围；整表操作不受支持")
        if self.operation != "select" and (self.group_by or self.aggregates or self.ratio):
            raise ValueError("写计划不能包含统计")
        if self.extremum and (self.operation != "select" or self.group_by or self.aggregates or self.ratio):
            raise ValueError("对应行极值只用于明细查询，不能混用分组、聚合、占比或写入")
        if self.relation_id and self.operation != "select":
            raise ValueError("已确认关联只用于查询，不能联表写入")
        if self.operation == "metadata":
            if self.metadata is None or self.values or self.fields or self.filters:
                raise ValueError("元数据修改只能包含metadata中的登记修改项")
        elif self.metadata is not None:
            raise ValueError("只有metadata操作能包含元数据修改")
        return self


ChatInput.model_rebuild()
