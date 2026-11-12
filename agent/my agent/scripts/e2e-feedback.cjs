/**
 * 八项试用反馈的独立浏览器验收。
 * 使用真实 Compose API / PostgreSQL / RQ 和明确的 mock 模型；新增虚构数据保留，不触碰业务配置。
 */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const { chromium } = createRequire(path.resolve(__dirname, '../frontend/package.json'))('playwright');

const root = path.resolve(__dirname, '..');
const baseUrl = process.env.VIBE_URL || 'http://localhost:8080';
const artifacts = path.join(root, 'test-results');
const credentials = Object.fromEntries(fs.readFileSync(path.join(root, '.env.acceptance'), 'utf8').split(/\r?\n/)
  .filter(line => line.includes('='))
  .map(line => { const separator = line.indexOf('='); return [line.slice(0, separator), line.slice(separator + 1)]; }));
fs.mkdirSync(artifacts, { recursive: true });

async function waitUntil(check, label, timeout = 60000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const value = await check();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`Timed out: ${label}`);
}

function assertOk(response, label) {
  assert(response.ok(), `${label}: HTTP ${response.status()}`);
  return response;
}

async function json(response, label) {
  return assertOk(response, label).json();
}

// 不把 Cookie 或凭据加入报告；报告只保留虚构样例 ID、验证项与公开状态。
const report = {
  status: 'running', model: 'mock only; no external model calls', database: 'real PostgreSQL',
  queue: 'real Redis / RQ', passed: [], screenshots: [], page_errors: [], failures: [], resources: {},
};

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_CHANNEL ? { channel: process.env.BROWSER_CHANNEL } : {}) });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.on('pageerror', error => report.page_errors.push(error.message));
  let headers;
  const requestJson = async (method, apiPath, data) => json(await context.request[method](`${baseUrl}/api${apiPath}`, {
    ...(method === 'get' ? {} : { headers }), ...(data === undefined ? {} : { data }),
  }), `${method.toUpperCase()} ${apiPath}`);
  const get = apiPath => requestJson('get', apiPath);
  const post = (apiPath, data) => requestJson('post', apiPath, data);
  const patch = (apiPath, data) => requestJson('patch', apiPath, data);
  const screenshot = async name => {
    await page.screenshot({ path: path.join(artifacts, name), fullPage: false });
    report.screenshots.push(name);
  };
  const importCsv = async (name, content, decorateFields = fields => fields) => {
    const uploaded = await json(await context.request.post(`${baseUrl}/api/files`, {
      headers, multipart: { file: { name: `${name}.csv`, mimeType: 'text/csv', buffer: Buffer.from(content) } },
    }), 'CSV upload');
    await waitUntil(async () => {
      const task = await get(`/tasks/${uploaded.task_id}`);
      assert.notEqual(task.status, 'failed', `Parsing failed: ${task.error || 'unknown error'}`);
      return task.status === 'completed';
    }, 'RQ parser');
    const preview = await get(`/imports/${uploaded.task_id}`);
    const candidate = preview.tables[0];
    const imported = await post(`/imports/${uploaded.task_id}/confirm`, {
      version: preview.version, tables: [{ candidate_id: candidate.candidate_id, name,
        description: '反馈验收虚构数据；不是公司业务数据', description_provenance: 'user',
        fields: decorateFields(candidate.fields), reviewed: true }],
    });
    assert.equal(imported.table_ids.length, 1);
    return { ...await get(`/tables/${imported.table_ids[0]}`), file_id: uploaded.file_id, task_id: uploaded.task_id };
  };

  try {
    // 必须在发送任何助手问题前检查；避免把自动化 E2E 误当真实模型联调。
    const readiness = await get('/ready');
    assert.equal(readiness.model_mode, 'mock', 'Feedback E2E requires the explicit acceptance mock overlay; no model request was sent');
    await page.goto(baseUrl);
    await page.getByLabel('账号', { exact: true }).fill(credentials.USERNAME);
    await page.getByLabel('密码', { exact: true }).fill(credentials.PASSWORD);
    await page.getByRole('button', { name: /^登\s*录$/ }).click();
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    await page.getByText('测试替身', { exact: true }).waitFor();
    const me = await get('/auth/me');
    headers = { 'X-CSRF-Token': me.csrf_token };
    report.passed.push('生产页面登录；mock 明确标注；凭据不进入报告');

    const nonce = Date.now();
    const primaryName = `反馈成绩-${nonce}`;
    const primary = await importCsv(primaryName, '学号,班级,成绩\n003,二班,80\n001,一班,90\n002,一班,50\n004,二班,\n', fields => fields.map(field => (
      field.name === '成绩' ? { ...field, aliases: ['分数', '得分'], unit: '分', provenance: 'user' } : field
    )));
    report.resources.primary_table_id = primary.id;
    report.resources.source_file_id = primary.file_id;
    assert.deepEqual(primary.rows.map(row => row.values.c1), ['003', '001', '002', '004']);
    assert.equal(primary.rows[3].values.c3, null);
    const secondPage = await get(`/tables/${primary.id}?offset=1&limit=2`);
    assert.deepEqual(secondPage.rows.map(row => row.values.c1), ['001', '002']);
    const exportTask = await post(`/tables/${primary.id}/exports`);
    await waitUntil(async () => (await get(`/tasks/${exportTask.task_id}`)).status === 'completed', 'CSV export');
    const exported = await context.request.get(`${baseUrl}/api/tasks/${exportTask.task_id}/download`);
    assertOk(exported, 'CSV export download');
    const lines = (await exported.text()).replace(/^\uFEFF/, '').trim().split(/\r?\n/);
    assert.equal(lines.length, 5);
    assert.deepEqual(lines.slice(1).map(line => line.split(',')[0]), ['003', '001', '002', '004']);
    report.passed.push('真实 RQ 导入、分页和 CSV 导出保持输入顺序、前导零及空值');

    const conversation = await post('/conversations', { title: `反馈验收-${nonce}` });
    report.resources.conversation_id = conversation.id;
    const newRun = (content, plan, tableIds = [primary.id]) => post(`/conversations/${conversation.id}/runs`, {
      content, table_ids: tableIds, ...(plan ? { plan } : {}),
    });
    const finishRun = run => waitUntil(async () => {
      const saved = await get(`/runs/${run.id}`);
      assert(!['failed', 'cancelled'].includes(saved.status), `Run ended as ${saved.status}: ${saved.error || ''}`);
      return saved.status === 'completed' ? saved : undefined;
    }, 'saved assistant run');
    const ask = async (content, plan, tableIds) => {
      const run = await finishRun(await newRun(content, plan, tableIds));
      assert(run.response, 'Completed run must retain a server response');
      return run.response;
    };
    const expectPending = async response => {
      assert(response.plan?.id, 'The assistant must explain a saved plan before execution');
      assert.equal(response.plan.status, 'pending');
      assert(!response.result, 'Planning must not execute a query or save a result');
      const persistedPlan = await get(`/plans/${response.plan.id}`);
      assert.equal(persistedPlan.status, 'pending');
      return persistedPlan;
    };
    const initial = await ask('查看前10条');
    const initialPlan = await expectPending(initial);
    assert.equal(initialPlan.operation, 'select');
    assert.equal((await get(`/conversations/${conversation.id}`)).results.length, 0);
    const initialQuery = await post(`/plans/${initialPlan.id}/confirm`);
    assert.deepEqual(initialQuery.result.rows.map(row => row.c1), ['003', '001', '002', '004']);
    const replay = await post(`/plans/${initialPlan.id}/confirm`);
    assert.equal(replay.result.id, initialQuery.result.id);
    report.resources.original_order_result_id = initialQuery.result.id;
    report.passed.push('查询先解释/预览；确认前无结果；确认与重复确认共用唯一快照；默认查询保留原始顺序');

    const aliased = await ask('查看分数大于等于80，按分数降序');
    const aliasedPlan = await expectPending(aliased);
    assert.equal(aliasedPlan.plan.filters[0].field_id, 'c3');
    assert.equal(aliasedPlan.plan.sort[0].field_id, 'c3');
    const adjusted = await ask('查看得分大于等于90，按得分降序');
    const adjustedPlan = await expectPending(adjusted);
    assert.equal((await get(`/plans/${aliasedPlan.id}`)).status, 'cancelled');
    const oldConfirmation = await context.request.post(`${baseUrl}/api/plans/${aliasedPlan.id}/confirm`, { headers });
    assert.equal(oldConfirmation.status(), 409, 'Superseded plans must not execute');
    const adjustedQuery = await post(`/plans/${adjustedPlan.id}/confirm`);
    assert.deepEqual(adjustedQuery.result.rows.map(row => row.c1), ['001']);
    report.resources.adjusted_result_id = adjustedQuery.result.id;
    const cancelled = await expectPending(await ask('查看分数大于等于80'));
    await post(`/plans/${cancelled.id}/cancel`);
    assert.equal((await get(`/plans/${cancelled.id}`)).status, 'cancelled');
    assert.equal((await get(`/conversations/${conversation.id}`)).results.length, 2);
    report.passed.push('登记别名解析成受控字段；跟进问题替换旧计划；取消查询不产生结果');

    const originalMetadata = await get(`/tables/${primary.id}`);
    const metadataPlan = await expectPending(await ask('将成绩字段说明改为反馈验收期末成绩', {
      operation: 'metadata', table_id: primary.id,
      metadata: { description: '反馈验收成绩表说明', field_changes: [
        { field_id: 'c3', description: '反馈验收期末成绩', unit: '分', aliases: ['分数', '得分'] },
      ] },
    }));
    assert.equal((await get(`/tables/${primary.id}`)).description, originalMetadata.description);
    await post(`/plans/${metadataPlan.id}/confirm`);
    const newMetadata = await get(`/tables/${primary.id}`);
    assert.equal(newMetadata.description, '反馈验收成绩表说明');
    assert.equal(newMetadata.description_provenance, 'user');
    const scoreField = newMetadata.fields.find(field => field.id === 'c3');
    assert.equal(scoreField.description, '反馈验收期末成绩');
    assert.equal(scoreField.provenance, 'user');
    assert.deepEqual(scoreField.aliases, ['分数', '得分']);
    report.passed.push('助手元数据计划先预览；确认后表/字段说明、单位与别名持久保存且标为用户输入');

    const resultCount = (await get(`/conversations/${conversation.id}`)).results.length;
    const stoppable = await newRun('查看前10条');
    await waitUntil(async () => (await get(`/runs/${stoppable.id}`)).status === 'running', 'running mock request', 15000);
    await post(`/runs/${stoppable.id}/cancel`);
    await waitUntil(async () => (await get(`/runs/${stoppable.id}`)).status === 'cancelled', 'persistent cancellation');
    await new Promise(resolve => setTimeout(resolve, 2500));
    const stoppedConversation = await get(`/conversations/${conversation.id}`);
    assert.equal(stoppedConversation.results.length, resultCount);
    const stopped = await get(`/runs/${stoppable.id}`);
    assert.equal(stopped.status, 'cancelled');
    if (stopped.response?.plan?.id) assert.notEqual((await get(`/plans/${stopped.response.plan.id}`)).status, 'pending');
    report.resources.cancelled_run_id = stoppable.id;
    report.passed.push('真实后台 running 状态服务端中止；等待后仍取消且无可执行计划或额外结果');

    const studentName = `反馈学生-${nonce}`;
    const students = await importCsv(studentName, '学号,姓名\n001,甲同学\n002,乙同学\n003,丙同学\n');
    report.resources.student_table_id = students.id;
    const candidates = await get(`/relations/candidates?table_id=${primary.id}`);
    assert(candidates.suggestions.some(candidate => new Set([candidate.left_table_id, candidate.right_table_id]).has(primary.id)
      && new Set([candidate.left_table_id, candidate.right_table_id]).has(students.id) && candidate.requires_confirmation));
    const relationPreview = await post('/relations/preview', {
      conversation_id: conversation.id, left_table_id: primary.id, right_table_id: students.id,
      left_field_id: 'c1', right_field_id: 'c1', join_type: 'left',
    });
    const relationPlan = await expectPending(relationPreview);
    assert.equal(relationPlan.plan.preview.cardinality, 'one_to_one');
    assert.equal(relationPlan.plan.preview.inner_rows, 3);
    assert.equal(relationPlan.plan.preview.joined_rows, 4);
    assert.equal(relationPlan.plan.preview.unmatched_left_rows, 1);
    assert(!(await get('/relations')).relations.some(relation => relation.id === relationPlan.plan.relation_id));
    await post(`/plans/${relationPlan.id}/confirm`);
    const relation = (await get('/relations')).relations.find(item => item.id === relationPlan.plan.relation_id);
    assert.equal(relation.status, 'active');
    const joined = await expectPending(await ask('核对已确认学号关联，保留所有成绩记录', {
      operation: 'select', table_id: primary.id, relation_id: relation.id,
      fields: ['left__c1', 'left__c3', 'right__c2'],
    }, [primary.id, students.id]));
    const joinedQuery = await post(`/plans/${joined.id}/confirm`);
    assert.deepEqual(joinedQuery.result.rows.map(row => row.left__c1), ['003', '001', '002', '004']);
    assert.deepEqual(joinedQuery.result.rows.map(row => row.right__c2), ['丙同学', '甲同学', '乙同学', null]);
    assert(joinedQuery.result.query_snapshot.relation || joinedQuery.result.query_snapshot.relation_id,
      'The snapshot must identify the confirmed relationship');
    report.resources.relation_id = relation.id;
    report.resources.joined_result_id = joinedQuery.result.id;
    report.passed.push('可靠身份候选不自动生效；双表连接基数/未匹配记录预览；确认后真实 left 联表保留左顺序与未匹配项');

    const anonymous = await browser.newContext();
    for (const protectedPath of [`/runs/${stoppable.id}`, `/plans/${initialPlan.id}`, `/results/${initialQuery.result.id}`, '/relations', `/tables/${primary.id}`]) {
      assert.equal((await anonymous.request.get(`${baseUrl}/api${protectedPath}`)).status(), 401);
    }
    assert.equal((await anonymous.request.delete(`${baseUrl}/api/results/${initialQuery.result.id}/visibility`)).status(), 401);
    assert.equal((await context.request.delete(`${baseUrl}/api/results/${initialQuery.result.id}/visibility`)).status(), 403);
    await anonymous.close();
    report.passed.push('匿名不能读取运行/计划/结果/关联/表；隐藏写请求必须携带 CSRF');

    await page.evaluate(({ account, id }) => localStorage.setItem(`vibe:${account}:conversation`, id), { account: me.id, id: conversation.id });
    await page.reload();
    await page.locator(`[data-result-id="${initialQuery.result.id}"]`).nth(1).waitFor();
    assert.equal(await page.locator(`[data-result-id="${initialQuery.result.id}"]`).count(), 2);
    await page.getByRole('heading', { name: '当前对话涉及的表', exact: true }).waitFor();
    const selectOption = async (label, text) => {
      const control = page.getByRole('combobox', { name: label, exact: true });
      // Ant Select 的已选文字会覆盖搜索 input；键盘打开遵循真实可访问交互，不强行点穿覆盖层。
      await control.focus();
      await control.press('ArrowDown');
      // 同时存在多个下拉层时，aria-controls 把选项范围绑定到当前输入，避免误选另一字段的同名选项。
      const listId = await control.getAttribute('aria-controls');
      assert(listId, `Missing popup relation for ${label}`);
      const popup = page.locator('.ant-select-dropdown').filter({ has: page.locator(`[id="${listId}"]`) });
      await popup.getByText(text, { exact: true }).click();
    };
    await selectOption('全部数据表', primaryName);
    await page.getByRole('heading', { name: primaryName, level: 1 }).waitFor();

    for (const [label, restored] of [['数据目录宽度', '244'], ['数据助手宽度', '390']]) {
      const separator = page.getByRole('separator', { name: label });
      const original = await separator.getAttribute('aria-valuenow');
      const box = await separator.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + 80);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width / 2 + 24, box.y + 80, { steps: 4 });
      await page.mouse.up();
      assert.notEqual(await separator.getAttribute('aria-valuenow'), original);
      await separator.focus(); await separator.press('ArrowLeft'); await separator.dblclick();
      assert.equal(await separator.getAttribute('aria-valuenow'), restored);
    }
    report.passed.push('桌面保留侧栏拖拽、方向键微调、双击默认宽度；全部表下拉及当前对话表上下文');

    await page.getByRole('button', { name: '修改对话名称', exact: true }).click();
    const renamed = `已重命名反馈-${nonce}`;
    report.resources.conversation_title = renamed;
    await page.getByLabel('对话名称', { exact: true }).fill(renamed);
    await page.getByRole('button', { name: '保存名称', exact: true }).click();
    await waitUntil(async () => (await get(`/conversations/${conversation.id}`)).title === renamed, 'saved conversation title');
    await page.reload();
    await page.getByRole('combobox', { name: '选择对话', exact: true }).waitFor();
    assert.equal((await get(`/conversations/${conversation.id}`)).title, renamed);
    assert((await page.locator('.conversation-select .ant-select-selection-item').textContent()).includes(renamed));
    await selectOption('全部数据表', primaryName);
    report.passed.push('桌面重命名对话；服务器与刷新后的标题一致');

    const question = page.getByLabel('向数据助手提问', { exact: true });
    await question.fill('查看前10条');
    const [createdRunResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === 'POST'
        && response.url().endsWith(`/api/conversations/${conversation.id}/runs`)),
      page.getByRole('button', { name: /发\s*送$/ }).click(),
    ]);
    const createdRun = await json(createdRunResponse, 'UI create run');
    report.resources.cancelled_ui_run_id = createdRun.id;
    await page.getByRole('button', { name: /中止$/ }).waitFor();
    assert(await question.isEditable(), 'The input must stay editable while the assistant is processing');
    const nextDraft = '等待时保留的下一问题草稿';
    await question.fill(nextDraft);
    assert.equal(await question.inputValue(), nextDraft);
    const [cancelledRunResponse] = await Promise.all([
      page.waitForResponse(response => response.request().method() === 'POST'
        && response.url().endsWith(`/api/runs/${createdRun.id}/cancel`)),
      page.getByRole('button', { name: /中止$/ }).click(),
    ]);
    assertOk(cancelledRunResponse, 'UI server cancellation');
    await waitUntil(async () => (await get(`/runs/${createdRun.id}`)).status === 'cancelled', 'UI cancelled run');
    assert.equal(await question.inputValue(), nextDraft);
    report.passed.push('桌面发送后继续输入；中止按钮调用服务器取消并保留下一问题草稿');

    const waitUiPlan = async operation => {
      const dialog = page.getByRole('dialog', { name: '操作计划与影响预览', exact: true });
      await dialog.locator(`[data-plan-operation="${operation}"]`).waitFor({ timeout: 60000 });
      return { dialog, id: await dialog.locator('[data-plan-id]').getAttribute('data-plan-id') };
    };
    const confirmUiPlan = async (operation, buttonName) => {
      const shown = await waitUiPlan(operation);
      const confirmButton = shown.dialog.getByRole('button', { name: buttonName, exact: true });
      // 窄屏长预览需要滚动；先检查可点击性，再监听请求，避免动画/视口问题被响应超时掩盖。
      await confirmButton.scrollIntoViewIfNeeded();
      await confirmButton.click({ trial: true });
      const confirmation = page.waitForResponse(response => response.request().method() === 'POST'
        && response.url().endsWith(`/api/plans/${shown.id}/confirm`)).catch(error => ({ waitError: error }));
      await confirmButton.click();
      const completed = await confirmation;
      if (completed.waitError) throw new Error(`UI ${operation} confirmation: ${completed.waitError.message}`);
      const saved = await json(completed, 'UI confirmation');
      await shown.dialog.getByRole('button', { name: /^关\s*闭$/ }).click();
      await shown.dialog.waitFor({ state: 'hidden' });
      return saved;
    };
    const sendUi = async content => {
      await question.fill(content);
      await page.getByRole('button', { name: /发\s*送$/ }).click();
    };
    await sendUi('查看分数大于等于80，按分数降序');
    const followUp = await waitUiPlan('select');
    assert.equal((await get(`/plans/${followUp.id}`)).plan.filters[0].value, 80);
    await followUp.dialog.getByRole('button', { name: '追问修改此计划', exact: true }).click();
    await sendUi('查看得分大于等于90，按得分降序');
    const followed = await waitUiPlan('select');
    assert.notEqual(followed.id, followUp.id);
    assert.equal((await get(`/plans/${followUp.id}`)).status, 'cancelled');
    await followed.dialog.getByRole('button', { name: '取消此计划', exact: true }).click();
    await waitUntil(async () => (await get(`/plans/${followed.id}`)).status === 'cancelled', 'UI plan cancellation');
    await followed.dialog.getByRole('button', { name: /^关\s*闭$/ }).click();
    report.passed.push('桌面别名问题展示下一步；追问重新预览并取消旧计划；界面取消不执行');

    await sendUi('将成绩字段说明改为桌面反馈成绩说明');
    const uiMetadata = await waitUiPlan('metadata');
    assert.notEqual((await get(`/tables/${primary.id}`)).fields.find(field => field.id === 'c3').description, '桌面反馈成绩说明');
    await screenshot('feedback-desktop-plan.png');
    await confirmUiPlan('metadata', '确认执行修改表与字段说明');
    assert.equal((await get(`/tables/${primary.id}`)).fields.find(field => field.id === 'c3').description, '桌面反馈成绩说明');
    report.passed.push('桌面自然语言助手修改字段说明：确认前不改、确认后保存');

    await page.getByRole('button', { name: /管理关联$/ }).click();
    await page.getByRole('heading', { name: '手工登记关联', exact: true }).waitFor();
    await selectOption('关联左表', primaryName);
    await selectOption('关联右表', studentName);
    await selectOption('关联左字段', '学号 (string)');
    await selectOption('关联右字段', '学号 (string)');
    await selectOption('连接方式', '左连接：保留全部左表记录');
    await page.getByRole('button', { name: '预览手工关联', exact: true }).click();
    const manualRelationPreview = await waitUiPlan('relation');
    const manualRelationPlan = await get(`/plans/${manualRelationPreview.id}`);
    assert.equal(manualRelationPlan.plan.preview.joined_rows, 4);
    assert.equal(manualRelationPlan.plan.preview.cardinality, 'one_to_one');
    await screenshot('feedback-desktop-relation.png');
    await confirmUiPlan('relation', '确认执行建立或调整关联');
    assert((await get('/relations')).relations.some(item => item.id === manualRelationPlan.plan.relation_id && item.status === 'active' && !item.stale));
    report.resources.ui_relation_id = manualRelationPlan.plan.relation_id;
    report.passed.push('桌面手工选择双表身份字段/左连接，核对基数预览后确认登记；旧版本关联不自动重启');

    await page.getByRole('button', { name: /管理关联$/ }).click();
    const oldRelationRow = page.locator(`[data-relation-id="${relation.id}"]`);
    assert(await oldRelationRow.getByRole('button', { name: '选用此关联查询', exact: true }).isDisabled());
    await page.locator(`[data-relation-id="${manualRelationPlan.plan.relation_id}"]`)
      .getByRole('button', { name: '选用此关联查询', exact: true }).click();
    await page.getByRole('button', { name: '结构化计划', exact: true }).click();
    await page.getByLabel('结构化查询计划 JSON', { exact: true }).fill(JSON.stringify({ operation: 'select',
      table_id: primary.id, relation_id: manualRelationPlan.plan.relation_id,
      fields: ['left__c1', 'left__c3', 'right__c2'], limit: 100 }));
    await page.getByRole('button', { name: '校验并生成预览', exact: true }).click();
    const uiJoinedPlan = await waitUiPlan('select');
    const frozenScope = uiJoinedPlan.dialog.locator(`[data-relation-scope-id="${manualRelationPlan.plan.relation_id}"]`);
    await frozenScope.waitFor();
    const scopeText = await frozenScope.textContent();
    for (const text of [primaryName, studentName, '连接基数：一对一', '左连接：保留全部左表记录', '空缺连接键', '未匹配记录']) {
      assert(scopeText.includes(text), `Readable fixed relation scope is missing: ${text}`);
    }
    assert(scopeText.includes(`v${manualRelationPlan.plan.left_version}`));
    assert(scopeText.includes(`v${manualRelationPlan.plan.right_version}`));
    await screenshot('feedback-desktop-joined-query.png');
    const uiJoined = await confirmUiPlan('select', '确认执行查询');
    assert.deepEqual(uiJoined.result.rows.map(row => row.left__c1), ['003', '001', '002', '004']);
    report.resources.ui_joined_result_id = uiJoined.result.id;
    report.passed.push('桌面联表查询预览展示中文双端名称/固定版本/键/基数/空值和未匹配项；失效关联不可直接选用；确认后数据库执行');

    const visibleCopies = page.locator(`[data-result-id="${initialQuery.result.id}"]`);
    assert.equal(await visibleCopies.count(), 2);
    await page.getByRole('button', { name: `隐藏结果 ${initialQuery.result.id}`, exact: true }).click();
    await waitUntil(async () => await visibleCopies.count() === 0, 'shared result hidden in both surfaces');
    const storedOriginal = await get(`/results/${initialQuery.result.id}`);
    assert.deepEqual(storedOriginal.rows, initialQuery.result.rows);
    const history = await get('/history?limit=200');
    assert(history.operations.some(item => item.details?.result_id === initialQuery.result.id));
    const otherConversation = await post('/conversations', { title: `显示偏好切换验收-${nonce}` });
    await page.evaluate(({ account, id }) => localStorage.setItem(`vibe:${account}:conversation`, id), { account: me.id, id: otherConversation.id });
    await page.reload();
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    await waitUntil(async () => (await get('/catalog')).hidden_result_ids.includes(initialQuery.result.id), 'account-wide hidden state');
    assert.equal(await visibleCopies.count(), 0);
    await page.evaluate(({ account, id }) => localStorage.setItem(`vibe:${account}:conversation`, id), { account: me.id, id: conversation.id });
    await page.reload();
    await page.getByRole('heading', { name: '我的数据', exact: true }).waitFor();
    assert.equal(await visibleCopies.count(), 0);
    await page.getByText(/^已隐藏结果 \(\d+\) · 可恢复显示$/).click();
    await page.getByRole('button', { name: `恢复显示 ${initialQuery.result.id}`, exact: true }).click();
    await visibleCopies.nth(1).waitFor();
    assert.equal(await visibleCopies.count(), 2);
    report.passed.push('结果双端同步隐藏；切换对话/刷新仍隐藏；服务器快照和审计保留；可恢复显示');
    await screenshot('feedback-desktop.png');

    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), 'Narrow page must not overflow');
    await page.getByRole('button', { name: /新建空表$/ }).click();
    const manualName = `窄屏手工订单-${nonce}`;
    await page.getByLabel('新表名称', { exact: true }).fill(manualName);
    await page.getByLabel('新表说明', { exact: true }).fill('窄屏创建的虚构手工表');
    await page.getByRole('button', { name: /添加字段$/ }).click();
    await page.getByRole('textbox', { name: /^字段名称 f_/ }).fill('订单号');
    await page.getByRole('button', { name: '预览创建空表', exact: true }).click();
    const createPreview = await waitUiPlan('create_table');
    assert(!(await get('/catalog')).tables.some(table => table.name === manualName));
    await screenshot('feedback-narrow-create.png');
    const createdTable = await confirmUiPlan('create_table', '确认执行新建数据表');
    const manualId = createdTable.plan.table_id;
    report.resources.manual_table_id = manualId;
    assert.equal((await get(`/tables/${manualId}`)).row_count, 0);
    await page.getByRole('heading', { name: manualName, level: 1 }).waitFor();
    await sendUi('新增 订单号=00009');
    const inserted = await confirmUiPlan('insert', '确认执行 1 条变更');
    assert.equal(inserted.result.rows[0][(await get(`/tables/${manualId}`)).fields[0].id], '00009');
    await page.getByRole('button', { name: /按页查看记录$/ }).click();
    await page.getByText('00009', { exact: true }).last().waitFor();
    // Drawer 的关闭可访问名称受 Ant Design 语言配置影响；限定当前打开抽屉的真实关闭按钮。
    await page.locator('.ant-drawer-open .ant-drawer-close').click();
    await page.getByRole('button', { name: /归档此表$/ }).click();
    await waitUiPlan('archive_table');
    assert((await get('/catalog')).tables.some(table => table.id === manualId));
    await confirmUiPlan('archive_table', '确认执行归档数据表');
    assert(!(await get('/catalog')).tables.some(table => table.id === manualId));
    const archivedData = await get(`/tables/${manualId}`);
    assert.equal(archivedData.archived, true);
    assert.equal(archivedData.row_count, 1);
    assert.equal(archivedData.rows[0].values[archivedData.fields[0].id], '00009');
    await page.getByRole('button', { name: '已归档表', exact: true }).click();
    await page.getByRole('button', { name: `预览恢复 ${manualName}`, exact: true }).click();
    await screenshot('feedback-narrow-restore.png');
    await confirmUiPlan('restore_table', '确认执行恢复数据表');
    assert((await get('/catalog')).tables.some(table => table.id === manualId));
    assert.equal((await get(`/tables/${manualId}`)).rows[0].values[archivedData.fields[0].id], '00009');
    report.passed.push('390px 窄屏创建空表、前导零记录预览确认、分页查看、归档和恢复；数据/结果/审计保留');

    await selectOption('全部数据表', primaryName);
    await question.fill('按班级统计分数达标率，阈值60，分母全部记录，使用饼图');
    await question.press('Control+Enter');
    await waitUiPlan('select');
    const narrowQuery = await confirmUiPlan('select', '确认执行查询');
    assert.equal(narrowQuery.result.chart.type, 'bar');
    assert(narrowQuery.result.explanation.includes('分子') && narrowQuery.result.explanation.includes('分母'));
    await page.locator(`[data-result-id="${narrowQuery.result.id}"]`).nth(1).waitFor();
    assert.equal(await page.locator(`[data-result-id="${narrowQuery.result.id}"]`).count(), 2);
    report.resources.narrow_result_id = narrowQuery.result.id;
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
    await screenshot('feedback-narrow.png');
    report.passed.push('390px 窄屏 Ctrl+Enter 发送、查询计划确认、同一双端快照与独立分母柱图');

    // 留下一项真实服务器隐藏偏好，供后续服务重启/容器重建的只读恢复验证。
    await requestJson('delete', `/results/${adjustedQuery.result.id}/visibility`);
    report.resources.hidden_result_id = adjustedQuery.result.id;
    report.resources.primary_metadata = { description: (await get(`/tables/${primary.id}`)).description,
      score_description: (await get(`/tables/${primary.id}`)).fields.find(field => field.id === 'c3').description };
    report.resources.source_file_sha256 = require('node:crypto').createHash('sha256')
      .update(await (await context.request.get(`${baseUrl}/api/files/${primary.file_id}/download`)).body()).digest('hex');
    // 专用 Cookie 文件仅保存在 Git 忽略目录，供隔离恢复验证；不覆盖旧版验收会话。
    await context.storageState({ path: path.join(root, '.env.feedback-session.json') });

    assert.deepEqual(report.page_errors, []);
    report.status = 'passed';
  } catch (error) {
    report.status = 'failed';
    report.failures.push(error.message);
    await screenshot('feedback-failure.png').catch(() => {});
    process.exitCode = 1;
  } finally {
    report.completed_at = new Date().toISOString();
    fs.writeFileSync(path.join(artifacts, 'feedback-browser-report.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ status: report.status, passed: report.passed.length, model: report.model, failures: report.failures }));
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
