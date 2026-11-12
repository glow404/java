/**
 * 浏览器验收入口。
 * 使用专用验收账号验证生产构建、RQ 导入、真实数据库查询与确认流程；模型为明确的 mock。
 */
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const assert = require('node:assert/strict');
const requireFrontend = createRequire(path.resolve(__dirname, '../frontend/package.json'));
const { chromium } = requireFrontend('playwright');

const root = path.resolve(__dirname, '..');
const env = Object.fromEntries(fs.readFileSync(path.join(root, '.env.acceptance'), 'utf8').split(/\r?\n/).filter(line => line.includes('=')).map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));
const url = process.env.VIBE_URL || 'http://localhost:8080';
const artifacts = path.join(root, 'test-results');
fs.mkdirSync(artifacts, { recursive: true });

async function waitUntil(check, label, timeout = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await check()) return;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error(`Timed out: ${label}`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const report = [];
  try {
    // 验收入口主动拒绝真实模型模式，避免维护旧回归时意外调用外部接口。
    const readyResponse = await context.request.get(`${url}/api/ready`);
    assert.ok(readyResponse.ok(), `readiness HTTP ${readyResponse.status()}`);
    assert.equal((await readyResponse.json()).model_mode, 'mock', '此脚本只允许 mock 模式；请先启动 compose.acceptance.yaml');
    await page.goto(url);
    await page.getByLabel('账号', { exact: true }).fill(env.USERNAME);
    await page.getByLabel('密码', { exact: true }).fill(env.PASSWORD);
    await page.getByRole('button', { name: /^登\s*录$/ }).click();
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    await page.getByText('测试替身', { exact: true }).waitFor();
    report.push('生产页面登录，mock 明确标注');

    const left = page.getByRole('separator', { name: '数据目录宽度' });
    const right = page.getByRole('separator', { name: '数据助手宽度' });
    for (const separator of [left, right]) {
      const original = Number(await separator.getAttribute('aria-valuenow'));
      const box = await separator.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + 80);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 30, box.y + 80, { steps: 5 });
      await page.mouse.up();
      assert.notEqual(Number(await separator.getAttribute('aria-valuenow')), original);
      await separator.focus(); await separator.press('ArrowLeft');
      await separator.dblclick();
    }
    assert.equal(await left.getAttribute('aria-valuenow'), '244');
    assert.equal(await right.getAttribute('aria-valuenow'), '390');
    report.push('左右侧栏拖拽、键盘、双击恢复默认');

    const selectTable = async name => {
      const control = page.getByRole('combobox', { name: '全部数据表', exact: true });
      // 从完整目录搜索，而不是假设历史会话快捷栏中一定包含这张表。
      await control.focus(); await control.press('ArrowDown'); await control.fill(name);
      await page.locator('.ant-select-dropdown:visible').getByText(name, { exact: true }).click();
      await page.getByRole('heading', { name, level: 1, exact: true }).waitFor();
    };
    const dismissImportRelations = async () => {
      // 新版导入后先展示可核对的关联建议；本回归保留单表验收范围，不自动确认关联。
      const drawer = page.getByRole('dialog', { name: '表关联与联表查询', exact: true });
      await drawer.waitFor();
      await drawer.locator('.ant-drawer-close').click();
      await drawer.waitFor({ state: 'hidden' });
    };
    const confirmPreview = async (operation, buttonName) => {
      const dialog = page.getByRole('dialog', { name: '操作计划与影响预览', exact: true });
      const preview = dialog.locator(`[data-plan-operation="${operation}"]`);
      await preview.waitFor({ timeout: 60000 });
      const planId = await preview.getAttribute('data-plan-id');
      const [confirmation] = await Promise.all([
        page.waitForResponse(response => response.request().method() === 'POST' && response.url().endsWith(`/api/plans/${planId}/confirm`)),
        dialog.getByRole('button', { name: buttonName }).click(),
      ]);
      assert.ok(confirmation.ok(), `confirm ${operation}: HTTP ${confirmation.status()}`);
      const saved = await confirmation.json();
      assert.equal(saved.plan.status, 'executed');
      assert.ok(saved.result?.id, '确认必须返回服务器保存的结果标识');
      return { saved, dialog };
    };

    const filename = `验收成绩-${Date.now()}.csv`;
    await page.getByLabel('选择数据文件', { exact: true }).setInputFiles({ name: filename, mimeType: 'text/csv',
      buffer: Buffer.from('学号,班级,成绩\n001,一班,90\n002,一班,50\n003,二班,80\n004,二班,\n') });
    await page.getByLabel('候选表名', { exact: true }).waitFor({ timeout: 60000 });
    const tableName = filename.replace('.csv', '');
    await page.getByLabel('候选行 2 成绩', { exact: true }).fill('55');
    await page.getByLabel('我已核对该表的字段、行数、汇总粒度和记录归属，确认以上修正', { exact: true }).check();
    await page.getByRole('button', { name: '确认选中表入库', exact: true }).click();
    await dismissImportRelations();
    await selectTable(tableName);
    report.push('真实上传 → RQ 解析 → 人工修正 → 事务入库');

    const me = await (await context.request.get(`${url}/api/auth/me`)).json();
    const headers = { 'X-CSRF-Token': me.csrf_token };
    const catalog = await (await context.request.get(`${url}/api/catalog`)).json();
    const table = catalog.tables.find(item => item.name === tableName);
    const saved = await (await context.request.get(`${url}/api/tables/${table.id}`)).json();
    assert.equal(saved.rows.find(row => row.values.c1 === '002').values.c3, 55);
    assert.equal(saved.rows.find(row => row.values.c1 === '004').values.c3, null);
    assert.equal(saved.rows[0].values.c1.length, 3);

    const ask = async text => {
      const previous = await page.locator('.workbench [data-result-id]').count();
      await page.getByLabel('向数据助手提问', { exact: true }).fill(text);
      await page.getByRole('button', { name: /发\s*送$/ }).click();
      const dialog = page.getByRole('dialog', { name: '操作计划与影响预览', exact: true });
      await dialog.locator('[data-plan-operation="select"]').waitFor({ timeout: 60000 });
      // 查询也要等待明确确认：看到预览时不能已经出现新结果或已执行的计划。
      assert.equal(await page.locator('.workbench [data-result-id]').count(), previous);
      const planId = await dialog.locator('[data-plan-id]').getAttribute('data-plan-id');
      const planResponse = await context.request.get(`${url}/api/plans/${planId}`);
      assert.ok(planResponse.ok());
      const pending = await planResponse.json();
      assert.equal(pending.status, 'pending'); assert.equal(pending.result_id, null);
      const confirmed = await confirmPreview('select', '确认执行查询');
      const resultId = confirmed.saved.result.id;
      await waitUntil(async () => await page.locator('.workbench [data-result-id]').count() > previous, 'saved query result after confirmation');
      await waitUntil(async () => await page.locator(`[data-result-id="${resultId}"]`).count() === 2, 'assistant/workbench shared snapshot');
      await confirmed.dialog.getByRole('button', { name: /^关\s*闭$/ }).click();
      await confirmed.dialog.waitFor({ state: 'hidden' });
      return resultId;
    };
    const firstResultId = await ask('查看成绩大于等于80，按成绩降序');
    const firstResult = await (await context.request.get(`${url}/api/results/${firstResultId}`)).json();
    assert.deepEqual(firstResult.rows.map(row => row.c3), [90, 80]);
    const ratioResultId = await ask('按班级统计成绩达标率，阈值60，分母全部记录，使用饼图');
    const ratioResult = await (await context.request.get(`${url}/api/results/${ratioResultId}`)).json();
    assert.equal(ratioResult.chart.type, 'bar');
    assert.ok(ratioResult.explanation.includes('分子') && ratioResult.explanation.includes('分母'));
    report.push('过滤排序、按班占比查询；独立分母柱图；助手与工作台同一结果 ID');

    await page.getByLabel('向数据助手提问', { exact: true }).fill('修改学号为001的成绩为95');
    await page.getByRole('button', { name: /发\s*送$/ }).click();
    await page.getByRole('button', { name: /确认执行.*条变更/ }).waitFor();
    const beforeConfirm = await (await context.request.get(`${url}/api/tables/${table.id}`)).json();
    assert.equal(beforeConfirm.rows.find(row => row.values.c1 === '001').values.c3, 90);
    const confirmedChange = await confirmPreview('update', /确认执行.*条变更/);
    const afterConfirm = await (await context.request.get(`${url}/api/tables/${table.id}`)).json();
    assert.equal(afterConfirm.rows.find(row => row.values.c1 === '001').values.c3, 95);
    await confirmedChange.dialog.getByRole('button', { name: /^关\s*闭$/ }).click();
    await confirmedChange.dialog.waitFor({ state: 'hidden' });
    report.push('修改先预览；确认前未写入；确认后仅影响目标记录');

    const otherName = `另一张验收表-${Date.now()}`;
    const secondUpload = await context.request.post(`${url}/api/files`, { headers, multipart: { file: { name: `${otherName}.csv`, mimeType: 'text/csv', buffer: Buffer.from('订单号,金额\n00009,12\n') } } });
    const task = await secondUpload.json();
    await waitUntil(async () => (await (await context.request.get(`${url}/api/tasks/${task.task_id}`)).json()).status === 'completed', 'second parsing');
    const preview = await (await context.request.get(`${url}/api/imports/${task.task_id}`)).json();
    const candidate = preview.tables[0];
    await context.request.post(`${url}/api/imports/${task.task_id}/confirm`, { headers, data: { version: preview.version, tables: [{ candidate_id: candidate.candidate_id, name: candidate.name, description: candidate.description, fields: candidate.fields, reviewed: true }] } });
    await page.getByRole('button', { name: /刷新目录$/ }).click();
    await selectTable(otherName);
    assert.equal(await page.locator(`[data-result-id="${firstResultId}"]`).count(), 2);
    await page.reload();
    await page.locator(`[data-result-id="${firstResultId}"]`).first().waitFor();
    await page.screenshot({ path: path.join(artifacts, 'desktop.png'), fullPage: true });
    report.push('切换数据表与刷新后保留多个持久快照');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(artifacts, 'narrow.png'), fullPage: true });
    const documentWidth = await page.evaluate(() => document.documentElement.scrollWidth);
    assert.ok(documentWidth <= 392, `narrow document overflow ${documentWidth}`);
    assert.ok(await page.getByLabel('向数据助手提问', { exact: true }).isVisible());
    report.push('390px 窄屏主要页面可访问，无页面级横向溢出');

    const narrowName = `窄屏验收成绩-${Date.now()}`;
    await page.getByLabel('选择数据文件', { exact: true }).setInputFiles({ name: `${narrowName}.csv`, mimeType: 'text/csv',
      buffer: Buffer.from('学号,班级,成绩\n005,一班,88\n006,二班,20\n') });
    await page.getByLabel('候选表名', { exact: true }).waitFor({ timeout: 60000 });
    await page.getByLabel('候选行 2 成绩', { exact: true }).fill('65');
    await page.getByLabel('我已核对该表的字段、行数、汇总粒度和记录归属，确认以上修正', { exact: true }).check();
    await page.screenshot({ path: path.join(artifacts, 'narrow-import.png') });
    await page.getByRole('button', { name: '确认选中表入库', exact: true }).click();
    await dismissImportRelations();
    await selectTable(narrowName);
    const narrowCatalog = await (await context.request.get(`${url}/api/catalog`)).json();
    const narrowTable = narrowCatalog.tables.find(item => item.name === narrowName);
    const narrowResultId = await ask('查看成绩大于等于80，按成绩降序');
    const narrowResult = await (await context.request.get(`${url}/api/results/${narrowResultId}`)).json();
    assert.deepEqual(narrowResult.rows.map(row => row.c3), [88]);
    await page.getByLabel('向数据助手提问', { exact: true }).fill('修改学号为005的成绩为92');
    await page.getByRole('button', { name: /发\s*送$/ }).click();
    await page.getByRole('button', { name: /确认执行.*条变更/ }).waitFor();
    await page.screenshot({ path: path.join(artifacts, 'narrow-plan.png') });
    assert.equal((await (await context.request.get(`${url}/api/tables/${narrowTable.id}`)).json()).rows.find(r => r.values.c1 === '005').values.c3, 88);
    const confirmedNarrowChange = await confirmPreview('update', /确认执行.*条变更/);
    assert.equal((await (await context.request.get(`${url}/api/tables/${narrowTable.id}`)).json()).rows.find(r => r.values.c1 === '005').values.c3, 92);
    await confirmedNarrowChange.dialog.getByRole('button', { name: /^关\s*闭$/ }).click();
    await confirmedNarrowChange.dialog.waitFor({ state: 'hidden' });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= 392);
    report.push('390px 窄屏实际完成上传、核对修正、查询、变更预览与确认');

    const persisted = { username: env.USERNAME, table_id: table.id, file_id: table.file_id, result_id: firstResultId,
      ratio_result_id: ratioResultId, conversation_id: firstResult.conversation_id, row_count: afterConfirm.total,
      updated_score: 95, file_sha256: require('node:crypto').createHash('sha256').update(await (await context.request.get(`${url}/api/files/${table.file_id}/download`)).body()).digest('hex') };
    fs.writeFileSync(path.join(artifacts, 'persistence.json'), JSON.stringify(persisted, null, 2));
    // 登录 Cookie 只写入 Git 忽略的本地验收文件，不进入报告或命令日志。
    await context.storageState({ path: path.join(root, '.env.acceptance-session.json') });
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(artifacts, 'browser-report.json'), JSON.stringify({ passed: report, page_errors: errors, model: 'mock only', database: 'real PostgreSQL' }, null, 2));
    console.log(JSON.stringify({ passed: report.length, model: 'mock only', screenshots: ['desktop.png', 'narrow.png', 'narrow-import.png', 'narrow-plan.png'] }));
  } catch (error) {
    await page.screenshot({ path: path.join(artifacts, 'failure.png'), fullPage: true }).catch(() => {});
    console.error(error.message); process.exitCode = 1;
  } finally { await browser.close(); }
})();
