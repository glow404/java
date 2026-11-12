# vibe 数据智能体

公司内网运行的第一版：登录后上传、核对并保存数据，再用中文生成受约束的数据库查询或变更预览。前端不直连数据库或模型。当前目录原先仅有策划资料，本版本是新增实现；没有修改旧演示或线上 D1。

## 快速启动（Windows PowerShell）

需要 Docker Desktop，启用 Linux 容器，并具备拉取镜像和 Python/npm 依赖的网络。第一次构建包含文档解析依赖，耗时比后续启动长。内网离线环境须提前构建并导入镜像。

```powershell
cd 'C:\Users\ZYH\Desktop\backend\agent\my agent'
powershell -ExecutionPolicy Bypass -File scripts/init-env.ps1
powershell -ExecutionPolicy Bypass -File scripts/start.ps1
docker compose exec api python -m app.cli create-user --username admin
```

初始化命令交互输入密码，不提供公开默认密码。访问 [本地工作台](http://localhost:8080)。`.env` 若已经存在，跳过初始化脚本，核对配置后直接启动。不要在命令行参数中明文传密码。账号创建命令也可供管理员创建第二个账号；各账号只访问自己的资源。

```powershell
docker compose ps
docker compose logs --tail 100 api worker
docker compose stop
docker compose up -d --wait --wait-timeout 300
```

升级时运行 `docker compose up -d --build --wait --wait-timeout 300`；`migrate` 服务先执行 Alembic 和检查点初始化。单独执行迁移使用 `docker compose run --rm migrate`。停止和重建均保留卷；不要使用删除卷的命令。首次启动若端口占用，修改 `.env` 的 `HTTP_PORT`。

Linux/macOS 同样使用 `cp .env.example .env`，设置随机 `POSTGRES_PASSWORD` 后运行上述 Compose 命令。`api` 可独立部署，启动命令是 `uvicorn app.main:app --host 0.0.0.0 --port 8000`，先配置 PostgreSQL/Redis/持久上传目录并运行迁移。

## 配置与内网部署

复制 `.env.example`；真实 `.env` 已被 Git 忽略。数据库密码、模型密钥仅在服务器配置；前端构建不包含密钥。`BIND_HOST` 默认 `127.0.0.1`，需要内网访问时由部署管理员明确配置监听地址，并用公司反向代理终止 HTTPS，设置 `COOKIE_SECURE=true` 和 `TRUSTED_ORIGINS=https://实际内网域名`。反向代理转发同源 `/api`，保留 Host 与正确的转发协议。禁止通过 HTTP 暴露正式登录入口。

模型默认 `MODEL_PROVIDER=disabled`。缺模型不会阻碍账号、文件、导入、分页查看、元数据、导出和历史功能。`mock` 是确定性测试替身，会在界面标注，不能代表模型能力或真实中转站联调。

接入中转站之前核对其**官方接口文档、基础地址、接口格式、模型标识和业务数据许可**。`openai_chat` 和 `openai_responses` 分别对应经验证的 chat-completions 与 Responses 协议；`generic_json` 按官方文档映射请求与返回，具体变量见 [模型适配与验收](docs/MODEL.md)。`MODEL_PROTOCOL_VERIFIED=true`、`MODEL_ALLOWED_DATA=true` 是部署者确认协议和数据授权后的显式门槛。未通过时拒绝请求。没有假设 JSON Schema、工具调用或流式支持；模型只返回经校验的计划，不执行其 SQL、代码或工具。

```dotenv
MODEL_PROVIDER=openai_chat
MODEL_BASE_URL=https://your-approved-relay.example/api
MODEL_NAME=your-approved-model-id
MODEL_API_KEY=
MODEL_PROTOCOL_VERIFIED=false
MODEL_ALLOWED_DATA=false
```

示例没有真实密钥。当前本地 `.env` 已按用户提供的信息配置云渡 Responses：`https://gd.yundu.lol/v1`、`gpt-5.6-sol`、`xhigh`、`store=false`，配置和真实调用证据见 [云渡联调记录](docs/RELAY_VERIFICATION.md)。新环境仍默认 disabled，须自行设置服务端密钥及许可。后台默认只发送字段元信息与必要的近期澄清文本；经许可并显式启用样本后，最多发送当前问题涉及的五个字段、三行短样本。查询和结果来自 PostgreSQL。

## 使用流程

1. 登录后首页汇总当前账号的数据表数量、表名和说明、管理入口及已确认关联；菜单切换首页、全部数据表、最近对话和个人主页，右侧 Agent 保持挂载。点开数据表才读取分页记录。
2. 上传 xlsx/csv/docx/文本 PDF，后台解析后核对原结构、候选、警告和来源。多个工作表/表格分别选择。修正名称、说明、类型、单位与数据后明确确认入库。
3. 选中一张或多张数据表向助手提问。助手先解释下一步查询或修改操作，核对计划后再确认执行，也可继续追问调整或取消。可合理判断的口径会列出建议、依据及替代项，用户可直接确认或修改。字段支持登记别名和有限常用同义词；未知字段及不唯一的映射仍要求澄清。含糊占比需补充阈值与分母。
4. 新增/修改/删除先展示影响数和前后值，再确认计划。过期、版本冲突或范围变化必须重新预览。重复确认只执行一次。助手可预览修改表名、表/字段说明、字段类型、单位与别名；用户原话标为用户输入，模型补充经确认后标为确认推断。
5. “全部数据表”进入独立详情页，每页20条，支持表资料管理、精确单行新增/修改/删除、导出和最近变更摘要。记录操作先预览再确认。永久删除表会清理记录、关联、相关结果/计划、工作流检查点及导出副本，保留对话文字和无原始数据的删除审计；共享源文件在最后一个表引用消失时才删除。已归档表仍可恢复。无显式排序的查看、查询及导出保持输入顺序。
6. 导入后查看关联建议，或在“表关联”中手工选择两端字段。核对空键、重复键和连接基数后确认登记，再同时选择两张表进行联表查询。第一版支持两表 inner/left 连接；表版本改变后必须重新核对关联，不凭姓名自动关联，禁止连接倍增造成的重复汇总。
7. 最近对话下拉显示最近一次用户提问，按最近活动排序；空对话显示“新对话”。手工标题仍可保存。助手等待时可继续输入草稿或中止运行；已经确认提交的操作需另建变更计划处理。隐藏结果保留快照，永久删除表则清除相关快照。
8. 个人主页可修改昵称、简介、头像及绑定邮箱。登录用户名保持固定，用户名或绑定邮箱均可登录。邮箱变更需当前密码，本期没有验证码邮件和邮箱找回密码。头像支持 JPG/PNG/WebP、不超过2MB。改密需验证旧密码，新密码至少12字符，成功后全部会话重新登录。

左右侧栏支持拖拽、方向键微调、双击恢复；宽度偏好保存于浏览器。对话、表、文件、任务、助手运行、结果、计划、关联、隐藏偏好和审计保存于服务器；保存失败可重试草稿。助手和工作台共用同一 `result_id` 与查询快照，切表不清空历史结果。新界面使用异步 `/runs` 接口；保留旧 `/messages` 接口供既有客户端兼容。

试用反馈实施与验收见 [实施标准](docs/FEEDBACK_IMPLEMENTATION.md) 和 [本轮测试报告](docs/FEEDBACK_TEST_REPORT.md)。0003 仅增量添加行序、表归档和反馈资源；旧行按可用导入索引或源行回填，完全缺失来源的旧行只能按记录 ID 稳定排序，无法凭空恢复输入顺序。升级前先运行下文的一致性备份；迁移不会清空数据，自动破坏性回退被禁用。

FRR/FAR 等比例指标支持百分数与小数比例换算：例如 `frr < 3%` 在小数比例存储中按 `frr < 0.03` 比较。空登记单位只能在本次计划中提出待确认解释，不自动改字段单位。求最优 FAR 及对应阈值会展示优化方向、筛选条件和全部并列最优记录；多个阈值字段缺少唯一依据时先建议同时返回候选列。具体设计及本轮验证见 [口径建议与单位修复](docs/ASSUMPTION_PREVIEW.md)。

## 文件边界

路由、个人资料及本轮验证见 [路由工作台说明](docs/ROUTED_WORKSPACE.md)。0004 增量增加账号资料，保留旧账号、表和对话；Compose 的 migrate 服务会在新 API 启动前执行迁移。

- xls 要先转换为 xlsx；不伪装支持旧格式。
- Excel 仅在明确垂直合并范围内展开归属，范围外空白保持空白。文本学号或格式化编号保留前导零，空成绩不变为零。公式保留原式和可用缓存，缓存缺失标记未计算。
- 多级表头、标题、源行、工作表/页码、合并范围均保留用于核对。未知身份或结构须核对，不能仅按姓名合并。
- 综合评价汇总与明细按粒度拆分须人工确认，禁止把汇总值复制到每条明细再重复求和。
- 扫描 PDF、跨页复杂表及不确定布局不能标注可靠解析。第一版使用 Docling Native PDF 管线，不启用 OCR 或下载推理权重；识别不到可靠表格时暂停并要求人工整理。
- 上传/解析/查询规模有上限，后台任务错误和重试可查询；本地第一版不承诺无限规模。

## 测试与验收

实施阶段与验收标准见 [实施文档](docs/IMPLEMENTATION.md)，接口见 [API 契约](docs/API.md)。最终执行证据见 [测试报告](docs/TEST_REPORT.md)。

```powershell
cd frontend
npm ci
npm test
npm run build
cd ..
docker compose exec -T postgres createdb -U vibe vibe_test
docker compose -f compose.yaml -f compose.test.yaml run --rm test python -m app.cli migrate
docker compose -f compose.yaml -f compose.test.yaml run --rm test
```

测试使用独立 `vibe_test` 数据库、Redis DB 1 和测试上传卷。数据库若已经存在，跳过 createdb；测试不清空应用数据库。前端测试替身与模型替身只用于自动化，真实 SQL 验收使用 PostgreSQL。端到端替身模式使用 `docker compose -f compose.yaml -f compose.acceptance.yaml up -d --build --wait`；结束后以默认 Compose 重新创建 API/worker 恢复 `.env` 中的模型配置。

Python 容器按 `backend/requirements.lock` 固定已验证的传递依赖；前端使用 `package-lock.json` 和 `npm ci`。升级依赖后重新执行测试、更新锁定文件并构建镜像。宿主 Python 如需开发，使用 Python 3.12 虚拟环境；锁定文件针对 Linux 容器，包含 Linux 专用的 uvloop。

浏览器验收使用真实 Compose API、PostgreSQL 和 RQ，只有模型是测试替身。先运行上面的前端 `npm ci`，Windows 可使用已安装的 Edge：

```powershell
docker compose -f compose.yaml -f compose.acceptance.yaml up -d --build --wait --wait-timeout 300
powershell -ExecutionPolicy Bypass -File scripts/prepare-acceptance.ps1
$env:BROWSER_CHANNEL='msedge'
node scripts/e2e.cjs
# 八项试用反馈的完整流程（同样仅使用明确的模型替身）：
node scripts/e2e-feedback.cjs

docker compose -f compose.yaml -f compose.acceptance.yaml restart api worker frontend
docker compose -f compose.yaml -f compose.acceptance.yaml up -d --wait --wait-timeout 300
$env:VERIFICATION_STAGE='service-restart'
node scripts/check-persistence.cjs
$env:FEEDBACK_PERSISTENCE_STAGE='service-restart'
node scripts/check-feedback-persistence.cjs

docker compose up -d --build --force-recreate --wait --wait-timeout 300
$env:VERIFICATION_STAGE='container-force-recreate'
node scripts/check-persistence.cjs
$env:FEEDBACK_PERSISTENCE_STAGE='container-force-recreate'
node scripts/check-feedback-persistence.cjs
```

没有 Edge 时，可用 `frontend/node_modules/.bin/playwright install chromium` 安装测试浏览器并移除 `BROWSER_CHANNEL` 环境变量。验收会新增专用账号和样例数据，全部保留，不清空业务数据。随机凭据和会话只写到被 Git 忽略的 `.env.acceptance*`、`.env.feedback-session.json`；测试报告和截图位于 `test-results/`。不要分享凭据或会话文件。单元测试的 HTTP 替身和浏览器的模型替身不等同于真实模型验收。

恢复 `.env` 的真实模型配置后，`python scripts/verify-feedback-relay.py` 可额外验证别名自然语言查询及助手说明修改的异步预览/确认。它只创建独立虚构表与对话，发送两条真实模型问题，可能触发适配器重试并产生服务商费用；不使用其他业务表。运行前核对 `MODEL_SEND_SAMPLES=false`。具体实际结果和边界见本轮测试报告。

执行上述真实模型验收后，可运行 `node scripts/check-feedback-relay-ui.cjs`，在桌面及窄屏复核其已保存结果在助手与工作台共用快照；该界面复核不再次调用模型。当前真实验收两条问题已通过，但规划分别耗时约182秒、33秒，不代表任意问题的准确率或延迟保证。

`python scripts/verify-assumption-relay.py` 单独复现 FRR/FAR 口径建议及用户补充后的预览/确认，使用新建虚构数据、两条真实模型问题，可能产生服务商费用。需要默认 Responses 配置、`MODEL_SEND_SAMPLES=false` 和专用 `.env.acceptance`；保留所有样例及失败环境，不读取 admin 业务数据行。结果写入被忽略的 `test-results/assumption-relay-report.json`。

## 备份与恢复

持久卷只是保留运行数据，不能抵抗误操作或磁盘损坏。备份应保存到独立存储，并控制访问权限。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/backup.ps1
powershell -ExecutionPolicy Bypass -File scripts/restore-test.ps1 -BackupDirectory 'C:\path\to\backups\timestamp'
# 已完成上述浏览器验收时，立即继续验证恢复后的应用行为：
powershell -ExecutionPolicy Bypass -File scripts/verify-restore.ps1 -BackupDirectory 'C:\path\to\backups\timestamp'
# 完成反馈端到端验收并保存独立反馈会话后，可追加新增资源恢复验证：
# 上面的行为验证会新增恢复库快照，先再次恢复到新的独立测试库：
powershell -ExecutionPolicy Bypass -File scripts/restore-test.ps1 -BackupDirectory 'C:\path\to\backups\timestamp'
powershell -ExecutionPolicy Bypass -File scripts/verify-restore.ps1 -BackupDirectory 'C:\path\to\backups\timestamp' -Feedback
```

备份脚本暂停本项目写入，使用 `pg_dump -Fc` 和上传文件归档，保存业务/检查点计数与 SHA256 清单，再启动服务。恢复脚本只恢复到新建测试数据库和新目录，校验归档哈希；不会覆盖正式数据。`verify-restore.ps1` 使用浏览器验收留下的账号、快照和当前会话，核对计数、所有源文件哈希、登录、查询、下载和检查点；应在备份/恢复后立即运行，期间不要重跑端到端脚本或更换验收会话。`-Feedback` 还核对顺序、别名、重命名、取消运行、关联、归档恢复、隐藏偏好以及恢复库中的实际查询；需要先完成 `e2e-feedback.cjs` 和 `check-feedback-persistence.cjs`。恢复行为测试只向新恢复库写入测试快照，每一种行为验证之前都须重新恢复到新库，否则新增验证快照会使计数与原备份不一致。每次恢复创建新的数据库，失败后修复脚本再运行 restore-test 即可，不覆盖失败环境。

正式恢复先演练，停止写入后经审阅切换数据库连接和上传目录；不要直接覆盖现有卷。Redis 是队列，任务与解析结果另存 PostgreSQL，可恢复未完成任务。备份脚本恢复服务时采用默认 `.env`，若原本使用验收 overlay，结束后会恢复为默认模型配置。

本配置使用 PostgreSQL 17。启动顺序依赖 [Compose 官方健康检查规则](https://docs.docker.com/compose/how-tos/startup-order/)；数据库恢复使用 [PostgreSQL 官方 pg_dump/pg_restore 流程](https://www.postgresql.org/docs/17/backup-dump.html)。旧 Worker/D1 的迁移、账号映射、校验和回滚方案见 [D1 迁移审阅](docs/D1-MIGRATION.md)，本次未执行线上迁移。

## 技术与核心机制

React/TypeScript/Vite 构建界面，Ant Design 提供组件，ECharts 展示查询数据；FastAPI/Pydantic 校验 API 与模型计划，SQLAlchemy Core 按字段注册表编译参数化查询，PostgreSQL 保存动态 JSONB 行。Alembic 管理结构迁移，LangGraph 使用 PostgreSQL 检查点，Redis/RQ 执行解析、异步助手规划和导出。openpyxl/python-docx/Docling 处理文件。

事务让变更与审计一起成功或回滚；行锁、表版本和计划状态防止重复或并发执行；服务端会话决定账号，所有资源再次检查归属。文件头和核心中文注释用于说明这些机制。
