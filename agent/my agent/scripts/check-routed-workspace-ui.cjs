/** 路由工作台真实浏览器回归：仅创建独立测试账号与虚构数据，不调用规划模型。 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { createRequire } = require('node:module');
const { chromium } = createRequire(path.resolve(__dirname, '../frontend/package.json'))('playwright');
const root = path.resolve(__dirname, '..');
const base = process.env.VIBE_URL || 'http://localhost:8080';
const reportPath = path.join(root, 'test-results/routed-workspace-ui-report.json');
const username = `route_ui_${crypto.randomBytes(6).toString('hex')}`;
const password = crypto.randomBytes(24).toString('hex') + '!Aa';
const nextPassword = crypto.randomBytes(24).toString('hex') + '!Bb';
const email = `${username}@example.test`;
const report = { status: 'running', checks: [], model_calls: 0, screenshots: [] };
let stage = 'create_test_account';
const checked = name => { report.checks.push(name); process.stdout.write(`passed: ${name}\n`); };
const redact = message => String(message).replaceAll(password, '[redacted]').replaceAll(nextPassword, '[redacted]');

(async () => {
  // 随机密码仅通过 stdin 传递，避免出现在命令参数、报告及持久会话文件中。
  const python = `from app.auth import create_user\nimport json,sys\nbody=json.load(sys.stdin)\ncreate_user(body['username'],body['password'])\n`;
  const created = spawnSync('docker', ['compose', 'exec', '-T', 'api', 'python', '-c', python], {
    cwd: root, input: JSON.stringify({ username, password }), encoding: 'utf8', windowsHide: true,
  });
  assert.equal(created.status, 0, 'isolated test account creation failed');
  const browser = await chromium.launch({ headless: true, channel: process.env.BROWSER_CHANNEL || 'msedge' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    stage = 'login';
    await page.goto(base);
    await page.getByLabel('用户名或邮箱', { exact: true }).fill(username);
    await page.getByLabel('密码', { exact: true }).fill(password);
    await page.getByRole('button', { name: /^登\s*录$/ }).click();
    await page.getByRole('heading', { name: '首页', exact: true }).waitFor();
    const get = async route => { const r = await context.request.get(base + '/api' + route); assert.equal(r.status(), 200, route); return r.json(); };
    let me = await get('/auth/me');
    const post = async (route, data) => {
      const r = await context.request.post(base + '/api' + route, { headers: { 'X-CSRF-Token': me.csrf_token }, ...(data ? { data } : {}) });
      assert.equal(r.status(), 200, route); return r.json();
    };
    stage = 'seed_csv';
    const csv = '学号,姓名,成绩\n001,测试A,90\n001,测试B,60\n' + Array.from({ length: 23 }, (_, i) => `${String(i + 3).padStart(3, '0')},测试${i + 3},${70 + i}`).join('\n');
    const uploaded = await context.request.post(base + '/api/files', { headers: { 'X-CSRF-Token': me.csrf_token }, multipart: { file: { name: 'route-ui.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) } } });
    assert.equal(uploaded.status(), 200);
    const upload = await uploaded.json();
    let task;
    for (let i = 0; i < 100; i += 1) { task = await get(`/tasks/${upload.task_id}`); if (['completed', 'failed'].includes(task.status)) break; await new Promise(resolve => setTimeout(resolve, 200)); }
    assert.equal(task.status, 'completed');
    const preview = await get(`/imports/${upload.task_id}`);
    const candidate = preview.tables.find(table => table.fields.map(f => f.name).join(',') === '学号,姓名,成绩');
    assert.ok(candidate);
    const imported = await post(`/imports/${upload.task_id}/confirm`, { version: preview.version, tables: [{ candidate_id: candidate.candidate_id, name: '路由回归数据', description: '独立账号虚构数据，含重复业务编号', fields: candidate.fields, reviewed: true }] });
    const tableId = imported.table_ids[0];
    const conversation = await post('/conversations', { title: '不应显示为提问的表名标题' });
    const otherPreview = await post('/tables/preview', { conversation_id: conversation.id, name: '路由回归关联表', description: '用于核对首页关联规则', fields: [{ id: 'c1', name: '学号', type: 'string' }] });
    const other = await post(`/plans/${otherPreview.plan.id}/confirm`);
    const otherTableId = other.plan.table_id;
    const relation = await post('/relations/preview', { conversation_id: conversation.id, left_table_id: tableId, right_table_id: otherTableId, left_field_id: 'c1', right_field_id: 'c1', join_type: 'left' });
    await post(`/plans/${relation.plan.id}/confirm`);
    // 明确提供受限结构化计划，仅验证真实 SQL 与对话标签，绕过模型调用。
    await post(`/conversations/${conversation.id}/messages`, { content: '第一次查看测试数据', table_ids: [tableId], plan: { operation: 'select', table_id: tableId, fields: ['c1', 'c3'], limit: 2 } });
    await post(`/conversations/${conversation.id}/messages`, { content: '最近一次查看测试成绩', table_ids: [tableId], plan: { operation: 'select', table_id: tableId, fields: ['c1', 'c3'], limit: 2 } });
    await page.evaluate(({ id, conversationId }) => localStorage.setItem(`vibe:${id}:conversation`, conversationId), { id: me.id, conversationId: conversation.id });
    await page.reload();
    await page.getByRole('heading', { name: '路由回归数据', exact: true }).waitFor();
    assert.equal((await get('/catalog')).tables.length, 2);
    assert.ok((await get('/relations')).relations.some(item => item.left_table_id === tableId));
    await page.locator('.ant-select[aria-label="最近对话"]').click();
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({ hasText: '最近一次查看测试成绩' }).waitFor();
    await page.keyboard.press('Escape');
    checked('home_cards_count_and_recent_question');
    stage = 'table_page';
    await page.getByRole('button', { name: '查看 路由回归数据', exact: true }).click();
    await page.waitForURL(`**/tables/${tableId}`);
    const original = await get(`/tables/${tableId}?offset=0&limit=20`);
    await page.getByRole('button', { name: `编辑记录 ${original.rows[1].id}`, exact: true }).waitFor();
    await page.getByTitle('2', { exact: true }).click();
    await page.getByRole('button', { name: `编辑记录 ${(await get(`/tables/${tableId}?offset=20&limit=20`)).rows[0].id}`, exact: true }).waitFor();
    await page.getByTitle('1', { exact: true }).click();
    await page.getByRole('button', { name: `编辑记录 ${original.rows[1].id}`, exact: true }).click();
    await page.getByLabel('记录字段 成绩', { exact: true }).fill('77');
    await page.getByRole('button', { name: '预览记录变更', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '操作计划与影响预览', exact: true });
    await dialog.getByRole('button', { name: /确认执行.*1.*条变更/ }).click();
    await page.getByText('已确认修改 1 条记录，按实际记录 ID 定位。', { exact: false }).first().waitFor();
    const changed = await get(`/tables/${tableId}?offset=0&limit=20`);
    assert.equal(changed.rows[0].values.c3, 90); assert.equal(changed.rows[1].values.c3, 77);
    assert.ok((await get(`/tables/${tableId}/changes?limit=10`)).operations.some(op => op.action === 'update'));
    await page.reload(); await page.getByRole('heading', { name: '路由回归数据', exact: true }).waitFor();
    checked('pagination_exact_row_confirm_and_deep_route_reload');
    stage = 'profile';
    const draft = '跨路由保留的未发送草稿';
    await page.getByLabel('向数据助手提问', { exact: true }).fill(draft);
    await page.getByRole('link', { name: /用户个人主页/ }).click();
    assert.equal(await page.getByLabel('向数据助手提问', { exact: true }).inputValue(), draft);
    await page.getByLabel('显示昵称', { exact: true }).fill('路由测试昵称');
    await page.getByLabel('个人简介', { exact: true }).fill('独立浏览器验收账号');
    await page.getByLabel('绑定邮箱', { exact: true }).fill(email);
    await page.getByLabel('邮箱变更验证密码', { exact: true }).fill(password);
    await page.getByRole('button', { name: '保存个人资料', exact: true }).click();
    await page.getByText('个人资料已保存。', { exact: true }).waitFor();
    const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=', 'base64');
    await page.getByLabel('上传头像', { exact: true }).setInputFiles({ name: 'avatar.png', mimeType: 'image/png', buffer: image });
    await page.getByText('头像已更新。', { exact: true }).waitFor();
    assert.ok((await get('/profile')).avatar_url);
    checked('draft_profile_email_and_avatar');
    stage = 'responsive';
    await page.setViewportSize({ width: 390, height: 844 });
    for (const route of ['/', `/tables/${tableId}`, '/profile']) {
      await page.goto(base + route); await page.getByLabel('向数据助手提问', { exact: true }).waitFor();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), route + ' overflows viewport');
      const screenshot = `test-results/routed-${route === '/' ? 'home' : route === '/profile' ? 'profile' : 'table'}-mobile.png`;
      await page.screenshot({ path: path.join(root, screenshot), fullPage: true }); report.screenshots.push(screenshot);
    }
    checked('mobile_routes_no_horizontal_overflow');
    stage = 'password';
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByLabel('修改密码的当前密码', { exact: true }).fill(password);
    await page.getByLabel('新密码', { exact: true }).fill(nextPassword);
    await page.getByLabel('确认新密码', { exact: true }).fill(nextPassword);
    await page.getByRole('button', { name: '修改密码并重新登录', exact: true }).click();
    await page.getByRole('button', { name: /^登\s*录$/ }).waitFor();
    await page.getByLabel('用户名或邮箱', { exact: true }).fill(email);
    await page.getByLabel('密码', { exact: true }).fill(nextPassword);
    await page.getByRole('button', { name: /^登\s*录$/ }).click();
    await page.getByRole('heading', { name: '用户个人主页', exact: true }).waitFor();
    me = await get('/auth/me');
    checked('password_reauthentication_and_email_login');
    stage = 'permanent_delete';
    await page.getByRole('link', { name: /首页/ }).click();
    await page.getByRole('button', { name: '永久删除 路由回归数据', exact: true }).click();
    await dialog.getByText('永久删除无法恢复', { exact: true }).waitFor();
    await dialog.getByRole('button', { name: '确认执行永久删除数据表', exact: true }).click();
    await page.getByText('已永久删除“路由回归数据”', { exact: false }).first().waitFor();
    assert.equal((await get('/catalog')).tables.length, 1);
    assert.ok((await get(`/conversations/${conversation.id}`)).results.every(result => result.table_id !== tableId));
    assert.deepEqual((await get('/relations')).relations, []);
    assert.equal((await context.request.get(base + '/api/files/' + upload.file_id + '/download')).status(), 404);
    checked('permanent_delete_snapshots_and_source_file');
    assert.deepEqual(errors, []); report.status = 'passed';
  } catch (error) {
    report.status = 'failed'; report.stage = stage; report.error = redact(error.message).slice(0, 2000);
    throw error;
  } finally {
    fs.mkdirSync(path.dirname(reportPath), { recursive: true });
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    await browser.close();
  }
})().catch(error => { process.stderr.write(`UI verification failed at ${stage}: ${redact(error.message).slice(0, 1200)}\n`); process.exitCode = 1; });
