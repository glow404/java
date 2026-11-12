/** 真实模型结果的界面复核：恢复已保存快照，不额外调用模型或修改数据。 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { chromium } = createRequire(path.resolve(__dirname, '../frontend/package.json'))('playwright');
const root = path.resolve(__dirname, '..');
const credentials = Object.fromEntries(fs.readFileSync(path.join(root, '.env.acceptance'), 'utf8').split(/\r?\n/).filter(line => line.includes('=')).map(line => {
  const separator = line.indexOf('='); return [line.slice(0, separator), line.slice(separator + 1)];
}));
const relay = JSON.parse(fs.readFileSync(path.join(root, 'test-results/relay-application-report.json'), 'utf8'));
assert.equal(relay.status, 'passed', 'Run the complete relay application verification first');
const url = process.env.VIBE_URL || 'http://localhost:8080';

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(url);
    await page.getByLabel('账号', { exact: true }).fill(credentials.USERNAME);
    await page.getByLabel('密码', { exact: true }).fill(credentials.PASSWORD);
    await page.getByRole('button', { name: /^登\s*录$/ }).click();
    await page.getByText('模型已配置', { exact: true }).waitFor();
    const me = await (await context.request.get(`${url}/api/auth/me`)).json();
    // 显式恢复刚完成真实联调的对话，避免受其他验收对话的排序影响。
    await page.evaluate(({ account, conversation }) => localStorage.setItem(`vibe:${account}:conversation`, conversation), { account: me.id, conversation: relay.conversation_id });
    await page.reload();
    const detail = await (await context.request.get(`${url}/api/conversations/${relay.conversation_id}`)).json();
    assert.equal(detail.results.length, 2);
    for (const result of detail.results) {
      const copies = page.locator(`[data-result-id="${result.id}"]`);
      await copies.nth(1).waitFor({ timeout: 30000 });
      assert.equal(await copies.count(), 2, 'Assistant and workspace must share one snapshot ID');
    }
    const averaged = detail.results.find(result => result.chart?.type === 'bar');
    assert(averaged);
    await page.locator(`[data-result-id="${averaged.id}"] canvas`).nth(1).waitFor({ timeout: 30000 });
    await page.screenshot({ path: path.join(root, 'test-results/relay-desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await page.screenshot({ path: path.join(root, 'test-results/relay-narrow.png'), fullPage: true });
    assert.deepEqual(errors, []);
    const report = { status: 'passed', model_mode: 'openai_responses', restored_results: detail.results.length, shared_copies_per_result: 2, chart_rendered: true, desktop_width: 1440, narrow_width: 390, page_errors: errors, extra_model_calls: 0 };
    fs.writeFileSync(path.join(root, 'test-results/relay-ui-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
