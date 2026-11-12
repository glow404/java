/** 建议口径的生产界面回归：只复用已验证计划和快照，不再次调用第三方模型。 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { chromium } = createRequire(path.resolve(__dirname, '../frontend/package.json'))('playwright');
const root = path.resolve(__dirname, '..');
const expected = JSON.parse(fs.readFileSync(path.join(root, 'test-results/assumption-relay-report.json'), 'utf8'));
assert.equal(expected.status, 'passed');
const base = process.env.VIBE_URL || 'http://localhost:8080';
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, storageState: path.join(root, '.env.feedback-session.json') });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    const get = async route => { const response = await context.request.get(base + route); assert.equal(response.status(), 200); return response.json(); };
    const me = await get('/api/auth/me');
    const post = async (route, data) => {
      const response = await context.request.post(base + route, { headers: { 'X-CSRF-Token': me.csrf_token }, ...(data ? { data } : {}) });
      assert.equal(response.status(), 200); return response.json();
    };
    const saved = await get(`/api/plans/${expected.plan_id}`);
    const conversation = await post('/api/conversations', { title: `口径界面复核-${Date.now()}` });
    // 提交已核对的结构化计划会经过同一服务端校验/RQ流程，不进入模型适配器。
    let run = await post(`/api/conversations/${conversation.id}/runs`, { content: '复核已保存口径，仅预览，不执行查询', table_ids: [expected.table_id], plan: saved.plan });
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline && ['queued', 'running'].includes(run.status)) {
      await new Promise(resolve => setTimeout(resolve, 200));
      run = await get(`/api/runs/${run.id}`);
    }
    assert.equal(run.status, 'completed');
    const planId = run.response.plan.id;
    assert.equal(run.response.plan.status, 'pending');
    assert.deepEqual((await get(`/api/conversations/${conversation.id}`)).results, []);
    await page.goto(base);
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    await page.evaluate(({ account, conversation }) => localStorage.setItem(`vibe:${account}:conversation`, conversation), { account: me.id, conversation: conversation.id });
    await page.reload();
    const dialog = page.getByRole('dialog', { name: '操作计划与影响预览', exact: true });
    const settleDialog = async () => {
      // 等有限入场动画完成再验证边界；中途截图会把缩放动画误当成窄屏裁切。
      await dialog.evaluate(async element => {
        await Promise.all(element.getAnimations({ subtree: true }).filter(animation => animation.effect.getTiming().iterations !== Infinity)
          .map(animation => animation.finished.catch(() => {})));
      });
      await page.waitForFunction(() => {
        const element = document.querySelector('[role="dialog"]');
        if (!element) return false;
        const bounds = element.getBoundingClientRect();
        return bounds.left >= -1 && bounds.right <= innerWidth + 1;
      });
    };
    if (!await dialog.isVisible()) await page.getByRole('button', { name: /查看操作预览与状态$/ }).last().click();
    await dialog.locator(`[data-plan-id="${planId}"]`).waitFor();
    await dialog.getByText('建议口径（待确认）', { exact: true }).waitFor();
    await settleDialog();
    const text = await dialog.innerText();
    assert(text.includes('frr（c4） < 0.03（小数比例）'));
    assert(text.includes('far（c5） 的最小值'));
    assert(text.includes('unlock_threshold（c2）'));
    assert(text.includes('全部并列最优记录'));
    assert(await dialog.getByRole('button', { name: '确认执行查询', exact: true }).isEnabled());
    await page.screenshot({ path: path.join(root, 'test-results/assumption-desktop.png') });
    await dialog.getByRole('button', { name: '追问修改此计划', exact: true }).click();
    await dialog.waitFor({ state: 'hidden' });
    await page.getByRole('textbox', { name: '向数据助手提问', exact: true }).fill('请改用另一个阈值字段，先重新预览');
    assert.equal(await page.getByRole('textbox', { name: '向数据助手提问', exact: true }).inputValue(), '请改用另一个阈值字段，先重新预览');
    await page.getByRole('button', { name: /查看操作预览与状态$/ }).last().click();
    await dialog.locator(`[data-plan-id="${planId}"]`).waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await settleDialog();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await page.screenshot({ path: path.join(root, 'test-results/assumption-narrow.png') });
    await dialog.getByRole('button', { name: '取消此计划', exact: true }).click();
    await dialog.getByText('cancelled', { exact: true }).waitFor();
    assert.equal((await get(`/api/plans/${planId}`)).status, 'cancelled');
    assert.deepEqual((await get(`/api/conversations/${conversation.id}`)).results, []);
    // 切回真实模型对话，助手和工作台必须读取同一保存结果，不能重新计算。
    await page.evaluate(({ account, conversation }) => localStorage.setItem(`vibe:${account}:conversation`, conversation), { account: me.id, conversation: expected.conversation_id });
    await page.reload();
    await page.locator(`[data-result-id="${expected.result_id}"]`).nth(1).waitFor();
    assert.equal(await page.locator(`[data-result-id="${expected.result_id}"]`).count(), 2);
    assert.deepEqual(errors, []);
    const report = { status: 'passed', model_calls: 0, page_errors: errors, run_id: run.id, plan_id: planId,
      passed: ['persisted assumptions and normalized steps on desktop', 'followup keeps editable draft', '390px preview and cancellation without query', 'real query shared snapshot restored in both places'] };
    fs.writeFileSync(path.join(root, 'test-results/assumption-ui-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
