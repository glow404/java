/** 真实模型新结果的界面复核：恢复同一已保存快照，不调用模型或执行变更。 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { chromium } = createRequire(path.resolve(__dirname, '../frontend/package.json'))('playwright');
const root = path.resolve(__dirname, '..');
const expected = JSON.parse(fs.readFileSync(path.join(root, 'test-results/feedback-relay-report.json'), 'utf8'));
assert.equal(expected.status, 'passed');
const baseUrl = process.env.VIBE_URL || 'http://localhost:8080';
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, storageState: path.join(root, '.env.feedback-session.json') });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const meResponse = await context.request.get(`${baseUrl}/api/auth/me`);
    assert.equal(meResponse.status(), 200);
    const me = await meResponse.json();
    await page.goto(baseUrl);
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    await page.evaluate(({ account, conversation }) => localStorage.setItem(`vibe:${account}:conversation`, conversation), { account: me.id, conversation: expected.conversation_id });
    await page.reload();
    await page.getByText('模型已配置', { exact: true }).waitFor();
    for (const id of [expected.query_result_id, expected.metadata_result_id]) {
      await page.locator(`[data-result-id="${id}"]`).nth(1).waitFor();
      assert.equal(await page.locator(`[data-result-id="${id}"]`).count(), 2);
    }
    await page.screenshot({ path: path.join(root, 'test-results/feedback-relay-desktop.png') });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await page.screenshot({ path: path.join(root, 'test-results/feedback-relay-narrow.png') });
    assert.deepEqual(errors, []);
    const report = { status: 'passed', model_calls: 0, passed: ['real model saved query and metadata snapshots', 'two copies per shared result ID', 'desktop and narrow rendering'], page_errors: errors };
    fs.writeFileSync(path.join(root, 'test-results/feedback-relay-ui-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
