/**
 * 反馈功能的只读持久恢复验收。
 * 使用已完成 E2E 的专用账号/资源，核对重启和重建后的名称、顺序、关联、中止、显示偏好与文件；不调用模型。
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { createRequire } = require('node:module');
const { chromium } = createRequire(path.resolve(__dirname, '../frontend/package.json'))('playwright');
const root = path.resolve(__dirname, '..');
const baseUrl = process.env.VIBE_URL || 'http://localhost:8080';
const stage = process.env.FEEDBACK_PERSISTENCE_STAGE || 'read-only-restoration';
assert(/^[a-z0-9-]+$/.test(stage), 'Stage must be a safe artifact identifier');
const baseline = JSON.parse(fs.readFileSync(path.join(root, 'test-results/feedback-browser-report.json'), 'utf8'));
assert.equal(baseline.status, 'passed', 'Complete the feedback browser E2E first');
const expected = baseline.resources;
const credentials = Object.fromEntries(fs.readFileSync(path.join(root, '.env.acceptance'), 'utf8').split(/\r?\n/)
  .filter(line => line.includes('='))
  .map(line => { const i = line.indexOf('='); return [line.slice(0, i), line.slice(i + 1)]; }));

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
  const sessionPath = path.join(root, '.env.feedback-session.json');
  const hasSession = fs.existsSync(sessionPath);
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, ...(hasSession ? { storageState: sessionPath } : {}) });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const get = async endpoint => {
    const response = await context.request.get(`${baseUrl}/api${endpoint}`);
    assert(response.ok(), `GET ${endpoint}: HTTP ${response.status()}`);
    return response.json();
  };
  try {
    // 重启/重建必须直接恢复原Cookie；重新登录不能掩盖服务器会话丢失。
    if (hasSession) assert.equal((await context.request.get(`${baseUrl}/api/auth/me`)).status(), 200, 'Saved server session must restore');
    else assert.equal(stage, 'baseline', 'Run the baseline first to save a server session');
    await page.goto(baseUrl);
    if (!hasSession) {
      await page.getByLabel('账号', { exact: true }).fill(credentials.USERNAME);
      await page.getByLabel('密码', { exact: true }).fill(credentials.PASSWORD);
      await page.getByRole('button', { name: /^登\s*录$/ }).click();
    }
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    const me = await get('/auth/me');
    const catalog = await get('/catalog');
    const table = await get(`/tables/${expected.primary_table_id}`);
    assert.deepEqual(table.rows.map(row => row.values.c1), ['003', '001', '002', '004']);
    assert.equal(table.rows[3].values.c3, null);
    assert.equal(table.description, expected.primary_metadata.description);
    assert.equal(table.fields.find(field => field.id === 'c3').description, expected.primary_metadata.score_description);
    assert.deepEqual(table.fields.find(field => field.id === 'c3').aliases, ['分数', '得分']);
    const conversation = await get(`/conversations/${expected.conversation_id}`);
    assert.equal(conversation.title, expected.conversation_title);
    for (const resultId of [expected.original_order_result_id, expected.adjusted_result_id, expected.joined_result_id,
      expected.ui_joined_result_id, expected.narrow_result_id]) {
      assert(conversation.results.some(result => result.id === resultId), 'Every saved snapshot must remain in the conversation');
      assert.equal((await get(`/results/${resultId}`)).id, resultId);
    }
    for (const runId of [expected.cancelled_run_id, expected.cancelled_ui_run_id]) {
      assert.equal((await get(`/runs/${runId}`)).status, 'cancelled');
    }
    assert(catalog.hidden_result_ids.includes(expected.hidden_result_id));
    assert(conversation.hidden_result_ids.includes(expected.hidden_result_id));
    const relations = await get('/relations');
    const relation = relations.relations.find(item => item.id === expected.ui_relation_id);
    assert.equal(relation.status, 'active');
    assert.equal(relation.stale, false);
    const manual = await get(`/tables/${expected.manual_table_id}`);
    assert.equal(manual.archived, false);
    assert.equal(manual.row_count, 1);
    assert.equal(manual.rows[0].values[manual.fields[0].id], '00009');
    const source = await context.request.get(`${baseUrl}/api/files/${expected.source_file_id}/download`);
    assert(source.ok());
    assert.equal(crypto.createHash('sha256').update(await source.body()).digest('hex'), expected.source_file_sha256);

    await page.evaluate(({ account, conversationId }) => localStorage.setItem(`vibe:${account}:conversation`, conversationId),
      { account: me.id, conversationId: expected.conversation_id });
    await page.reload();
    await page.locator(`[data-result-id="${expected.original_order_result_id}"]`).nth(1).waitFor();
    assert.equal(await page.locator(`[data-result-id="${expected.hidden_result_id}"]`).count(), 0);
    assert.equal(await page.locator(`[data-result-id="${expected.original_order_result_id}"]`).count(), 2);
    await page.screenshot({ path: path.join(root, `test-results/feedback-persistence-${stage}-desktop.png`), fullPage: false });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await page.screenshot({ path: path.join(root, `test-results/feedback-persistence-${stage}-narrow.png`), fullPage: false });
    assert.deepEqual(pageErrors, []);
    await context.storageState({ path: path.join(root, '.env.feedback-session.json') });
    const report = { status: 'passed', stage, model_calls: 0, persisted: [
      'saved server session', 'input order and empty values', 'table / field metadata and aliases', 'conversation title and snapshots',
      'server-side cancellation', 'hidden result preference', 'confirmed relationship', 'restored manual table rows', 'source SHA256',
      'desktop and narrow snapshot restoration',
    ], page_errors: pageErrors };
    fs.writeFileSync(path.join(root, `test-results/feedback-persistence-${stage}.json`), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
