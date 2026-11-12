# 云渡 Responses 配置与真实联调

日期：2026-10-08。本记录对应用户指定的云渡服务；没有配置其他应用的全局模型，没有提交密钥或发布生产。

## 配置依据

用户提供的客户端配置明确指定 `wire_api=responses`、`https://gd.yundu.lol`、`gpt-5.6-sol`、`xhigh`、关闭响应存储及附加认证头。实际模型目录验证了带连字符的模型标识。项目使用 `/v1` 作为基础路径，适配器追加 `/responses`；密钥只由后端从被 Git 忽略的 `.env` 读取。

查阅了 [云渡官方文档](https://doc.yundu.lol/) 和 [官方 Codex 桌面版教程](https://doc.yundu.lol/codex%E6%A1%8C%E9%9D%A2%E7%89%88%E4%BD%BF%E7%94%A8%E6%95%99%E7%A8%8B.html)。公开教程没有列出完整 Responses 请求与返回字段，因此本文不会声称完成了全部参数的官方文档认证。当前兼容性依据用户明确的协议配置及下述真实端点测试，不推广到其他中转站或接口功能。

不含密钥的完整配置见 [MODEL.md](MODEL.md)。当前 `MODEL_SEND_SAMPLES=false`，只发送所选表的字段元信息及必要的近期对话。联调专门创建虚构 CSV 与独立对话；没有发送已有业务表或整表。

## 实际调用证据

- `GET https://gd.yundu.lol/v1/models`：HTTP 200，目录包含 `gpt-5.6-sol`。
- `POST https://gd.yundu.lol/v1/responses`：仅使用虚构提示，HTTP 200，55.51 秒；返回模型 `gpt-5.6-sol`、状态 completed、`store=false`，output 包含 reasoning 和 assistant/message 文本，文本可校验为 JSON 澄清计划。
- 请求实测接受 `reasoning.effort=xhigh`、`stream=false`、`store=false`、Bearer 认证和用户指定附加头。没有发送工具、JSON Schema 或 response_format。
- 初次应用查询返回全空 ratio 对象，Pydantic 拒绝该计划并返回 502，没有执行查询或变更。已修正文本契约：不适用的 ratio 使用 null 或省略，字段缺失须澄清。保留严格拒绝及回归用例，没有自动修复模型计划。
- 另一次“先预览修改”被模型解释为 select，数据库只读取，没有写入。已在契约明确 insert/update/delete 本身仅生成待确认预览，真正写入仍须独立确认接口。修复后重跑完整链路，全部通过。

最终应用验收 `status=passed`，3 次真实模型调用和 4 项验收通过：

| 场景 | 实际结果 | 耗时 |
| --- | --- | --- |
| 按班级平均成绩及柱状图 | 一班 70、二班 80；空成绩排除；实际 SQL 与保存结果快照一致 | 28.85 秒 |
| 高分学生占比缺少定义 | 返回澄清问题，不生成查询结果或变更计划 | 49.28 秒 |
| 修改学号001成绩为93并先预览 | pending/update、影响1条、90→93；确认前数据库90，确认后数据库93；重复确认同一结果，修改审计1条 | 36.76 秒 |
| 运行配置 | API 目录和容器配置确认 openai_responses；模型/推理参数正确、样本关闭 | 无额外模型调用 |

脱敏协议报告：`test-results/relay-probe.json`。应用级验收报告：`test-results/relay-application-report.json`。报告不保存密钥、原始商业响应或推理内容。

随后使用 Edge/Playwright 登录生产页面，恢复上述真实对话：两个结果各自以同一 ID 在助手及工作台出现两次，平均值柱图实际渲染；1440px 桌面和390px窄屏检查通过，页面运行错误0，未额外调用模型。证据为 `test-results/relay-ui-report.json`、`relay-desktop.png`、`relay-narrow.png`。

## 可复现验证

先按 README 启动容器并配置后端模型。宿主 Python 安装 `httpx`、`python-dotenv`；下列脚本会创建并保留专用账号和虚构数据，不清空现有数据。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/prepare-acceptance.ps1
python scripts/verify-relay.py
$env:BROWSER_CHANNEL='msedge'
node scripts/check-relay-ui.cjs
```

脚本通过真实 HTTP/RQ 导入虚构成绩表，验证分班平均值及保存图表快照、占比缺阈值和分母时澄清、修改预览确认前后数据及重复确认一次执行。每轮新增独立表和对话，不删除先前验收数据。它会产生实际模型费用；后端配置必须是已验证的 openai_responses。

## 验收边界

最终后端 **86 项通过**，覆盖 Responses 参数/附加头/不存储、reasoning 与文本拆分、全空 ratio 的严格拒绝回归。故障、拒答、工具调用、未完成输出、网络失败、超时、限流和并发的自动化使用 MockTransport，明确属于测试替身。没有主动对真实商业服务制造限流或故障；这些真实服务故障场景、全部参数、工具和流式能力均未声称验收。

`store=false` 被接受不等于服务商所有日志或缓存都禁用。实际业务数据使用范围与保留约定仍由公司和服务商确认。当前只验证本机容器，HTTPS/公司内网部署验收另行执行。
