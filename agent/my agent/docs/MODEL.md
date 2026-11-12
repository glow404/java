# 中转站适配与验收记录

2026-10-08 已收到用户指定的云渡服务配置，并实测 `https://gd.yundu.lol/v1/models` 和 `/v1/responses`。配置模型 `gpt-5.6-sol` 在模型目录中存在，Responses 请求返回 HTTP 200、completed、JSON 文本及 `store=false`。密钥仅写入 Git 忽略的本地 `.env`，不进入本文、配置示例或前端。应用链路验收和限制见 [云渡联调记录](RELAY_VERIFICATION.md)。自动化故障测试仍使用明确标注的 `httpx.MockTransport`，不能冒充真实服务故障验收。

## 适配配置

`MODEL_PROVIDER=disabled` 为默认。`mock` 只理解若干测试句式：

- `查看全部记录`、`查看成绩大于等于80，按成绩降序`
- `按班级统计成绩平均值`
- `高分学生占比` 会要求澄清；`按班级统计成绩达标率，阈值80，分母全部记录` 才能执行。
- `修改学号为001的成绩为90`
- `删除学号为001的记录`
- `新增 学号=003，班级=一班，成绩=70`

必须以实际字段名为准；未知句式返回澄清而非编造回答。也可在助手高级计划入口填写严格 JSON 计划，仍经过同样的权限、字段、类型、范围和事务校验。

`openai_chat` 只有在服务商官方文档及实测支持时启用。`MODEL_BASE_URL` 是对应的 API 基础路径，适配器追加 `/chat/completions`；返回路径为 `choices.0.message.content`。仅发送常规文本消息和 `stream=false`，不发送工具、JSON Schema 或 response_format 参数。

`openai_responses` 追加 `/responses`，发送 `instructions`、文本 `input`、`stream=false`、`store` 和配置的 `reasoning.effort`。返回仅接受 completed 状态的 assistant/output_text；忽略 reasoning 项，拒绝工具调用、拒答及不完整内容，再用 Pydantic 校验计划。云渡当前已实测配置如下；复制时自行在服务端填密钥：

```dotenv
MODEL_PROVIDER=openai_responses
MODEL_BASE_URL=https://gd.yundu.lol/v1
MODEL_NAME=gpt-5.6-sol
MODEL_API_KEY=
MODEL_AUTH_HEADER=Authorization
MODEL_AUTH_PREFIX="Bearer "
MODEL_EXTRA_HEADERS={"x-openai-actor-authorization":"local-image-extension"}
MODEL_REASONING_EFFORT=xhigh
MODEL_STORE=false
MODEL_TIMEOUT=180
MODEL_RETRIES=1
MODEL_CONCURRENCY=4
MODEL_PROTOCOL_VERIFIED=true
MODEL_ALLOWED_DATA=true
MODEL_SEND_SAMPLES=false
```

用户提供的 `wire_api=responses` 对应 `MODEL_PROVIDER=openai_responses`；`disable_response_storage=true` 对应 `MODEL_STORE=false`。`requires_openai_auth=false` 是原客户端的设置，并不表示中转站无需认证：实测使用 Bearer 中转站密钥。`review_model`、图像生成和客户端 features 不属于本项目数据助手配置。没有修改全局 Codex 配置。`store=false` 只证明该参数被接口接受并返回，不证明服务方所有日志已关闭。

修改后运行 `docker compose up -d --build --wait --wait-timeout 300` 让容器重新读取配置；`docker compose restart` 不会载入修改后的环境变量。更换服务商、基础地址或协议后应将协议标记重置为 false 并重新验证，不能沿用云渡的验收结论。

2026-10-10 FRR/FAR 口径验收中，两次90秒等待均超时，本地等待上限调整为180秒，保留用户指定的xhigh和一次重试。随后两条真实规划完成，延迟仍可能较长；等待上限不是延迟承诺。记录见 [口径建议验收](ASSUMPTION_PREVIEW.md)。

`generic_json` 直接 POST `MODEL_BASE_URL` 指定的文档化端点。按官方协议配置 JSON 请求模板、返回路径和认证头，例如：

```dotenv
MODEL_PROVIDER=generic_json
MODEL_BASE_URL=https://your-approved-relay.example/generate
MODEL_NAME=your-approved-model-id
MODEL_API_KEY=
MODEL_REQUEST_TEMPLATE={"model_id":"$model","prompt":"$prompt","stream":false}
MODEL_RESPONSE_PATH=data.text
MODEL_AUTH_HEADER=X-API-Key
MODEL_AUTH_PREFIX=
MODEL_PROTOCOL_VERIFIED=false
MODEL_ALLOWED_DATA=false
MODEL_SEND_SAMPLES=false
```

模板只替换完整字符串 `$model` 和 `$prompt`，不执行表达式。返回路径按点分层，数组下标支持数字。若服务商协议不适合该映射，应新增适配类及协议测试，不能硬套兼容协议。协议核对通过、数据发送授权明确后，再设置两个门槛为 true。请求超时、重试与并发由 `MODEL_TIMEOUT`、`MODEL_RETRIES`、`MODEL_CONCURRENCY` 控制；失败保留用户消息。

## 数据与安全边界

发送当前选中表的字段元信息；默认不发送样本值。只有许可已明确且显式启用 `MODEL_SEND_SAMPLES=true`，才发送当前问题涉及的少量字段、最多三行短样本。文件原文、单元格和问题均标记为不可信资料。后端永不执行模型 SQL、代码或工具调用，未知属性、字段、表范围与不匹配单位会被拒绝。

模型内容必须是一个可直接校验的 JSON 计划；不从解释或 Markdown 代码块中猜测提取。结果数字与图表都来自服务器查询快照。低质量或恶意输出失败时，没有数据库写入。

## 真实联调所需验收

由部署管理员记录服务商、官方文档链接及版本、批准的数据范围、实际端点与模型标识；密钥只进入 `.env` 或服务器密钥管理。然后在脱敏测试账号实测：普通响应、严格 JSON 输出、拒答、超时、限流、网络失败、并发上限、参数兼容性和中文澄清。验证计划执行与预览/确认链路，确认服务商日志与数据保留约定。报告记录真实调用日期、成功与失败案例，不能用 mock 的通过结果替代。
