# API v1 契约

所有接口位于 `/api`；JSON 错误包含 `detail`。除登录和健康检查外均要求 HttpOnly 会话 Cookie。写请求需要 `/auth/me` 返回的 `csrf_token` 对应 `X-CSRF-Token`。不得接受 owner_id 或身份头作为身份凭据。ID 均为不透明字符串。

- `GET /health`、`GET /ready`：存活、数据库及 Redis 就绪。
- `POST /auth/login {username,password}`：username可为登录用户名或绑定邮箱；`GET /auth/me` 返回 `{id,username,csrf_token,display_name,bio,email,avatar_url}`；`POST /auth/logout`。

## 路由工作台与个人资料

- `GET /profile`：安全资料，不含密码摘要或会话ID。`PATCH /profile {display_name?,bio?,email?,current_password?}`：邮箱变更/解绑需当前密码，规范化并唯一。
- `POST /profile/avatar`：multipart file，JPG/PNG/WebP≤2MB，重编码PNG；`GET /profile/avatar`仅返回当前账号头像。
- `POST /profile/password {current_password,new_password}`：新密码至少12字符，撤销全部会话，返回 `{ok,reauthenticate}`并清Cookie。
- `GET /conversations`：增加最后一次用户问题`last_question`和派生`updated_at`，按最近活动排序。
- `POST /tables/{id}/rows/preview {conversation_id,version,operation,row_id?,values?}`：按唯一row_id生成单行计划，统一`POST /plans/{id}/confirm`确认。
- `GET /tables/{id}/changes?limit=10`：按当前表过滤后取最近变更，返回`{operations}`，每项含summary。
- `POST /tables/{id}/delete/preview {conversation_id,version}`：永久删除预览；统一确认入口返回`{plan,deleted_table_id,message}`，无指向已删表的结果。清理范围见[路由工作台说明](ROUTED_WORKSPACE.md)。活跃任务/范围变更409；文件清理失败503，可重复确认重试。
- `GET /catalog` 返回 `{tables:[{id,name,description,version,row_count,fields,file_id}],tasks:[],recent_operations:[],model_mode}`。
- `GET /tables/{id}?offset=0&limit=100` 返回表元数据及 `{rows:[{id,values,source}],total}`。
- `PATCH /tables/{id}` 接受 `{version,name,description,description_provenance?,fields:[{id,name,type,unit,description,provenance,type_provenance?}]}`；修改类型必须校验已有记录，不兼容值返回错误；改写原文的内容自动标为 user。
- `POST /files` multipart 字段 `file`；返回 `{file_id,task_id}`。`GET /files/{id}/download` 重新核对归属。
- `GET /tasks/{id}` 返回 `{id,status,error,attempts,kind,...}`；`POST /tasks/{id}/retry`；后台任务状态 pending/running/completed/failed。
- `GET /imports/{task_id}?candidate_id=...&offset=0&limit=200` 返回 `{task_id,version,tables:[{candidate_id,name,description,fields,rows,original,warnings,requires_review,row_count,row_offset}],warnings}`。每个 `rows` 元素含 `{values,source}`；预览按窗口加载，完整结果保存服务端，修正的 row_index 是候选全表索引。
- `POST /imports/{task_id}/confirm` 接受 `{version,tables:[{candidate_id,name,description,reviewed:true,fields:[...],corrections:[{row_index,field_id,value}]}]}`；返回 `{table_ids}`。字段 ID 固定，`type` 为 string/number/integer/boolean/date，来源为 file/user/confirmed_inference；幂等确认不重复入库。
- `GET /conversations`；`POST /conversations {title?}`；`GET /conversations/{id}` 返回 `{id,title,messages:[{id,role,content,result_id?,plan_id?,clarifications?}],results:[]}`。
- `POST /conversations/{id}/messages {content,table_ids:[...],plan?}` 返回 `{message,result?,plan?,clarifications?}`。可选 plan 是用户核对的结构化计划，经过同样的后端验证；模型不可用时返回可理解错误且用户消息持久化。
- `POST /conversations/{id}/messages/{message_id}/retry` 从服务器检查点恢复当前会话的失败消息；已经保存的结果/计划复用同一 ID，不重复执行。
- `GET /results/{id}` 返回 `{id,table_id,columns,rows,chart,explanation,query_snapshot,created_at}`；同一个结果在助手及工作台复用。
- `GET /plans/{id}` 返回 `{id,status,operation,table_id,table_version,affected_count,before,after,expires_at}`。
- `POST /plans/{id}/confirm`、`POST /plans/{id}/cancel` 请求不含写入内容，仅 URL 中的计划 ID；确认返回 `{plan,result}`。
- `POST /tables/{id}/exports` 创建后台 CSV 导出任务；`GET /tasks/{id}/download` 返回文件。
- `GET /history?limit=50` 返回 `{operations:[...]}`。

结构化模型计划：`operation` select/insert/update/delete/metadata/clarify，`table_id`，`fields` 字段 ID 列表，`filters:[{field_id,op,value}]`，`sort:[{field_id,direction}]`，`group_by`，`aggregates:[{field_id?,function,alias}]`，`limit`，`values`，`clarifications`，`chart`。过滤运算 eq/ne/gt/gte/lt/lte/contains/in/is_null；汇总 count/sum/avg/min/max。百分比需要显式 ratio `{field_id,threshold,denominator:"all"|"non_null"}`；按组独立占比用柱状图。后端二次校验表权限、字段类型、指标和图表。

## 试用反馈增量接口（0003）

这些接口沿用会话归属和 CSRF 门槛。新增确认仍仅提交 URL 中的计划标识。旧 `/messages` 查询行为保留兼容，新前端使用以下异步运行接口，所有查询和修改均先预览。

- `GET /tables?include_archived=false` 返回 `{tables}`；`GET /catalog` 增加 `archived_tables` 和账号范围的 `hidden_result_ids`。字段元信息增加 `aliases:string[]`；别名最多20项，必须唯一解析到登记字段，否则澄清。默认分页查询、导出和新行按服务器 `position` 排序。
- `POST /tables/preview {conversation_id,name,description?,fields}`：空表创建预览，确认前不创建表，返回 `{plan,message}`。`POST /tables/{id}/state/preview {conversation_id,version,archived}`：归档/恢复预览，确认不删除原数据、文件、结果或审计。
- `PATCH /conversations/{id} {title}`：保存非空标题；`GET /conversations/{id}` 消息增加当前账号核验过的 `table_ids`，返回账号范围 `hidden_result_ids` 使两个结果位置和跨对话刷新同步。
- `POST /conversations/{id}/runs {content,table_ids,plan?}` 返回 `{id,status,message_id,...}`；`GET /conversations/{id}/runs` 返回 `{runs}`；`GET /runs/{id}` 查询 queued/running/completed/failed/cancelled 状态。completed 的 `response` 为 `{message,plan?,clarifications?}`，确认计划后才生成结果。一个对话仅允许一个 queued/running 任务。
- `POST /runs/{id}/cancel`：保存取消状态、取消该运行未执行计划并通知 RQ 停止；输入草稿由前端保留。运行锁与确认事务使用一致顺序；已经确认的运行返回409，不能把已提交变更伪装成取消。新追问自动取消当前对话的未执行助手计划。
- `GET /plans/{id}` 增加 `intent` 与原结构化 `plan`；select 预览说明字段、过滤、统计和分页上限，基表记录数不等于查询结果数。metadata 计划的 `metadata:{name?,description?,field_changes:[{field_id,name?,description?,type?,unit?,aliases?}]}` 预览原值和目标值；模型补充内容标为 confirmed_inference。类型改变先验证已有全表数据并给出最多20条转换样本。
- `GET /relations` 返回 `{relations}`；`GET /relations/candidates?table_id=...`（兼容 `/relations/suggestions`）返回 `{suggestions,truncated}`。建议是待核对候选，不登记或执行关联。
- `POST /relations/preview {conversation_id,left_table_id,right_table_id,left_field_id,right_field_id,join_type:"inner"|"left"}`：保存键类型/单位、两端版本、空/重复键、匹配量和基数预览。确认后登记 `relation_id`。`POST /relations/{id}/state/preview {conversation_id,status:"inactive"}`：停用预览；重新启用必须重新核对并创建关联。
- 联表 select 计划传已确认 `relation_id`，主表为关联左表，当前选择范围包含两端。字段注册 ID 为 `left__原ID`/`right__原ID`；查询预览固定双端版本、键和真实连接基数，确认重新核对。禁止连接倍增后的不安全汇总；`count` 明确是连接记录数，不能当独立学生数。
- `DELETE /results/{id}/visibility` 隐藏页面结果；`POST /results/{id}/visibility` 恢复，均返回 `{result_id,hidden}`。这些操作只保存显示偏好，`GET /results/{id}` 和 `/history` 仍可核对原始快照及执行记录。

`assistant_runs` 的状态和原请求保存 PostgreSQL，RQ 分发失败会显示可恢复错误；工作进程恢复未完成运行时复用消息和工作流检查点。旧计划因会话失效、版本改变、过期或取消仍需重新预览。重启不会扩大原计划授权。

`GET /api/docs` 和 `/api/openapi.json` 也要求登录。`query_snapshot` 保存计划、字段注册表、表版本、参数化 SQL、绑定值与分页口径，不接受这些内容作为后续写入指令。计划确认没有请求体；执行成功但检查点恢复暂时失败时返回 `checkpoint_warning`，业务事务不会重复执行。

## 建议口径与比例单位

结构化计划新增可选 `assumptions:[{subject,choice,reason,alternatives:string[]}]`，保存于计划和查询快照。模型有依据时提出待确认解释，助手文字和预览展示依据与备选，用户确认、取消或继续提问修改。没有依据的关键指标、未知字段、身份归属、占比缺阈值/分母仍须澄清；建议本身不授予权限，也不改写字段元数据。旧 `/messages` 即使通常立即查询，带建议口径的计划也先预览。

过滤与占比阈值的 `unit` 表示边界数值当前采用的单位，支持 `%`/百分数与 `ratio`/小数比例的规范转换。登记单位有值时优先校验，冲突拒绝。登记单位为空时可用 `storage_unit:"ratio"|"percent"` 提出本次查询存储解释；例如 `{field_id:"c4",op:"lt",value:3,unit:"%",storage_unit:"ratio"}` 经后端换算为 `value:0.03`，保存规范化计划，重复确认不会再次除100。解释须预览确认，字段资料保持原样。无依据且只有百分号的空单位字段不能直接查询。

服务端仅对有限的比例指标做账号范围内 min/max 聚合，向模型提供 `rate_storage_hint` 候选类别及限制说明，不发送原始值或整表。值落在0至1不证明原始单位一定是小数比例，候选明确标为待确认。用户最近明确补充优先；否定句、疑问和“可能”不能作为用户已确认口径。

求最优值及其对应阈值/记录使用可选 `extremum:{field_id,direction:"min"|"max"}`。只允许明细数值查询，不能混用分组、聚合、占比或写入；数据库在相同账号、所选表和过滤范围内求极值，再返回所有并列对应记录，仍受分页及数量上限限制。空极值不参与计算。多个阈值字段无可靠选择依据时，建议同时返回各候选字段供核对。
