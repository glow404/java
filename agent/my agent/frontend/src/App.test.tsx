/** 工作台路由集成验收：页面分工、共享快照及草稿恢复使用本地 API 替身。 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App as AntApp, ConfigProvider } from 'antd';
import App from './App';
import ResizableSeparator from './ResizableSeparator';
import type { ChangePlan, QueryResult } from './types';

const fixture: QueryResult = { id: 'result-unique', table_id: 'table1', columns: [{ id: 'score', name: '成绩', type: 'number' }], rows: [{ score: 87 }], explanation: '数据库实际返回的成绩', query_snapshot: { operation: 'select', table_id: 'table1' }, created_at: '2026-10-08T01:00:00Z' };
const tables = [
  { id: 'table1', name: '成绩数据', description: '学生成绩原始记录', row_count: 3, version: 1, fields: [{ id: 'score', name: '成绩', type: 'number', provenance: 'file' }] },
  { id: 'table2', name: '第二张数据含可读完整表名', description: '另一张表', row_count: 4, version: 1, fields: [{ id: 'name', name: '姓名', type: 'string', provenance: 'file' }] },
];
// 路由状态独立于 React 挂载；每项验收从首页开始，避免上一项网址污染下一项。
beforeEach(() => { localStorage.clear(); window.history.replaceState(null, '', '/'); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
function pageRead(url: string, options: RequestInit = {}) {
  if (options.method && options.method !== 'GET') return undefined;
  const path = url.replace('/api', '');
  if (path === '/relations') return new Response(JSON.stringify({ relations: [] }));
  if (/^\/tables\/[^/]+\/changes\?limit=10$/.test(path)) return new Response(JSON.stringify({ operations: [] }));
  if (/^\/tables\/[^/]+\?offset=\d+&limit=20$/.test(path)) return new Response(JSON.stringify({ rows: [], total: 0 }));
  return undefined;
}
describe('工作台', () => {
  it('同名数据表显示短 ID，切表读取分页内容而不写入，发送携带明确选择的表 ID', async () => {
    const duplicateTables = [
      { ...tables[0], id: 'e25e1111222233334444555566667777', name: 'far_frr_thresholds_0.50_0.85' },
      { ...tables[0], id: 'ec690111222233334444555566667777', name: 'far_frr_thresholds_0.50_0.85' },
    ];
    const oldLabel = `${duplicateTables[0].name}（${duplicateTables[0].id.slice(0, 8)}）`;
    const selectedLabel = `${duplicateTables[1].name}（${duplicateTables[1].id.slice(0, 8)}）`;
    const savedResult = { ...fixture, table_id: duplicateTables[0].id };
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      let payload: unknown;
      if (path === '/auth/me') payload = { id: 'u1', username: '学习账号', csrf_token: 'csrf' };
      else if (path === '/catalog') payload = { tables: duplicateTables, tasks: [], recent_operations: [], model_mode: 'mock' };
      else if (path === '/conversations') payload = { conversations: [{ id: 'c1', title: '同名文件查询' }] };
      else if (path === '/conversations/c1') payload = { id: 'c1', title: '同名文件查询', messages: [{ id: 'm1', role: 'assistant', content: '已保存旧表结果', result_id: savedResult.id, table_ids: duplicateTables.map(table => table.id) }], results: [savedResult] };
      else if (path === '/conversations/c1/runs') payload = options.method === 'POST' ? { id: 'selected-run', status: 'cancelled', conversation_id: 'c1' } : { runs: [] };
      else throw new Error(`意外请求：${path}`);
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await screen.findByRole('heading', { level: 1, name: '首页' });
    await screen.findByRole('heading', { level: 2, name: oldLabel });
    expect(screen.getByRole('heading', { level: 2, name: selectedLabel })).toBeInTheDocument();
    await waitFor(() => expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(1));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '全部数据表' }));
    fireEvent.click(await screen.findByText(selectedLabel, { selector: '.table-option-label' }));
    await screen.findByRole('heading', { level: 1, name: selectedLabel });
    expect(container.querySelector('.assistant-context .ant-select-selection-item')).toHaveTextContent(selectedLabel);
    // 路由切表可读取记录，须主动提交提问才创建服务端写入任务。
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url.includes(`/tables/${duplicateTables[1].id}?offset=0&limit=20`))).toBe(true));
    expect(window.location.pathname).toBe(`/tables/${duplicateTables[1].id}`);
    expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('向数据助手提问'), { target: { value: 'frr<3%的情况下，far的最优值和对应的阈值是多少？' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /发送$/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /发送$/ }));
    await waitFor(() => {
      const sent = fetchMock.mock.calls.filter(([url, options]) => url === '/api/conversations/c1/runs' && options?.method === 'POST');
      expect(sent).toHaveLength(1);
      expect(JSON.parse(String(sent[0][1]?.body)).table_ids).toEqual([duplicateTables[1].id]);
    });
  });
  it('首页只加载目录，对话页与助手使用同一快照，切表保留助手结果', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const page = pageRead(url); if (page) return page;
      const path = url.replace('/api', '');
      let payload: unknown;
      if (path === '/auth/me') payload = { id: 'u1', username: '学习账号', csrf_token: 'csrf' };
      else if (path === '/catalog') payload = { tables, tasks: [], recent_operations: [], model_mode: 'disabled' };
      else if (path === '/conversations') payload = { conversations: [{ id: 'c1', title: '已保存的对话' }] };
      else if (path === '/conversations/c1/runs') payload = { runs: [] };
      else if (path === '/conversations/c1') payload = { id: 'c1', title: '已保存的对话', messages: [{ id: 'm1', role: 'assistant', content: '查询完成', result_id: fixture.id }], results: [fixture] };
      else throw new Error(`意外请求：${url}`);
      return new Response(JSON.stringify(payload), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await screen.findByText('查询完成');
    await waitFor(() => expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(1));
    expect(screen.getByRole('heading', { level: 1, name: '首页' })).toBeInTheDocument();
    expect(screen.getByText('中转站尚未配置')).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/tables/'))).toBe(false);
    window.history.pushState(null, '', '/conversations/c1'); window.dispatchEvent(new PopStateEvent('popstate'));
    await waitFor(() => expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(2));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '全部数据表' }));
    fireEvent.click(await screen.findByText('第二张数据含可读完整表名'));
    await screen.findByRole('heading', { level: 1, name: '第二张数据含可读完整表名' });
    expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(1);
    const separator = screen.getByRole('separator', { name: '数据目录宽度' });
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(separator).toHaveAttribute('aria-valuenow', '252');
    expect(localStorage.getItem('vibe:u1:width:left')).toBe('252');
    fireEvent.doubleClick(separator); expect(separator).toHaveAttribute('aria-valuenow', '244');
  });
  it('右侧分隔条方向键移动符合几何方向且可复位', () => {
    const onChange = vi.fn();
    render(<ResizableSeparator side="right" width={390} onChange={onChange} />);
    const separator = screen.getByRole('separator');
    fireEvent.keyDown(separator, { key: 'ArrowLeft' }); expect(onChange).toHaveBeenLastCalledWith(398);
    fireEvent.doubleClick(separator); expect(onChange).toHaveBeenLastCalledWith(390);
  });
  it('会话过期后重新登录保留未提交的问题草稿', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      if (path === '/conversations/c1/runs' && options.method === 'POST') return new Response(JSON.stringify({ detail: '会话已过期' }), { status: 401 });
      const payload = path === '/auth/me' ? { id: 'u1', username: '学习账号', csrf_token: 'new-csrf' }
        : path === '/auth/login' ? { id: 'u1' }
        : path === '/catalog' ? { tables, tasks: [], recent_operations: [], model_mode: 'disabled' }
        : path === '/conversations' ? { conversations: [{ id: 'c1', title: '已保存的对话' }] }
        : path === '/conversations/c1/runs' ? { runs: [] }
        : { id: 'c1', title: '已保存的对话', messages: [], results: [] };
      return new Response(JSON.stringify(payload), { status: 200 });
    }));
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    const draft = await screen.findByLabelText('向数据助手提问');
    fireEvent.change(draft, { target: { value: '查看成绩大于80的学生' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /发送$/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /发送$/ }));
    await screen.findByRole('dialog', { name: '登录会话已过期' });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'test-password-123' } });
    fireEvent.click(screen.getByRole('button', { name: '重新登录' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '登录会话已过期' })).not.toBeInTheDocument());
    expect(screen.getByLabelText('向数据助手提问')).toHaveValue('查看成绩大于80的学生');
  });
  it('异步提问等待时可编辑下一问题，中止请求发送至服务器且保留新草稿', async () => {
    let cancelled = false;
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      let payload: unknown;
      if (path === '/auth/me') payload = { id: 'u1', username: '学习账号', csrf_token: 'csrf' };
      else if (path === '/catalog') payload = { tables, tasks: [], recent_operations: [], model_mode: 'mock' };
      else if (path === '/conversations') payload = { conversations: [{ id: 'c1', title: '异步对话' }] };
      else if (path === '/conversations/c1') payload = { id: 'c1', title: '异步对话', messages: [], results: [] };
      else if (path === '/conversations/c1/runs') payload = options.method === 'POST' ? { id: 'run1', status: 'queued', conversation_id: 'c1' } : { runs: [] };
      else if (path === '/runs/run1/cancel') { cancelled = true; payload = { id: 'run1', status: 'cancelled', conversation_id: 'c1' }; }
      else if (path === '/runs/run1') payload = { id: 'run1', status: cancelled ? 'cancelled' : 'running', conversation_id: 'c1' };
      else throw new Error(`意外请求：${path}`);
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    const composer = await screen.findByLabelText('向数据助手提问');
    fireEvent.change(composer, { target: { value: '查看成绩' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /发送$/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /发送$/ }));
    await screen.findByText('中止');
    expect(composer).toBeEnabled();
    fireEvent.change(composer, { target: { value: '下一问题的未提交草稿' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /中止$/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /中止$/ }));
    await screen.findByText('本次提问已中止，没有执行未确认的操作。');
    expect(composer).toHaveValue('下一问题的未提交草稿');
    expect(fetchMock.mock.calls.some(([url, options]) => url === '/api/runs/run1/cancel' && options?.method === 'POST')).toBe(true);
  });
  it('刷新恢复未完成提问，不重新发送用户消息', async () => {
    const fetchMock = vi.fn(async (url: string) => {
      const page = pageRead(url); if (page) return page;
      const path = url.replace('/api', '');
      const payload = path === '/auth/me' ? { id: 'u1', username: '学习账号', csrf_token: 'csrf' }
        : path === '/catalog' ? { tables, tasks: [], recent_operations: [], model_mode: 'mock' }
        : path === '/conversations' ? { conversations: [{ id: 'c1', title: '恢复对话' }] }
        : path === '/conversations/c1/runs' ? { runs: [{ id: 'saved-run', status: 'running', conversation_id: 'c1' }] }
        : path === '/runs/saved-run' ? { id: 'saved-run', status: 'running', conversation_id: 'c1' }
        : { id: 'c1', title: '恢复对话', messages: [{ id: 'm1', role: 'user', content: '已保存的问题' }], results: [] };
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await screen.findByText('中止');
    expect(container.querySelector('[data-run-id="saved-run"]')).toBeInTheDocument();
    expect(screen.getByLabelText('向数据助手提问')).toBeEnabled();
    expect(fetchMock.mock.calls.every(([url]) => !url.endsWith('/messages'))).toBe(true);
  });
  it('队列离线提示请求已保存，恢复时清除提示，期间输入与服务端中止仍可用', async () => {
    const warning = '队列暂不可用；问题已保存，后台恢复后继续';
    let queueRecovered = false;
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const route = url.replace('/api', '');
      const payload = route === '/auth/me' ? { id: 'u1', username: '学习账号', csrf_token: 'csrf' }
        : route === '/catalog' ? { tables, tasks: [], recent_operations: [], model_mode: 'mock' }
        : route === '/conversations' ? { conversations: [{ id: 'c1', title: '离线恢复对话' }] }
        : route === '/conversations/c1/runs' ? options.method === 'POST' ? { id: 'offline-run', status: 'queued', conversation_id: 'c1' } : { runs: [] }
        : route === '/runs/offline-run' ? { id: 'offline-run', status: queueRecovered ? 'running' : 'queued', conversation_id: 'c1', error: queueRecovered ? null : warning }
        : route === '/runs/offline-run/cancel' ? { id: 'offline-run', status: 'cancelled', conversation_id: 'c1' }
        : { id: 'c1', title: '离线恢复对话', messages: [], results: [] };
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    const composer = await screen.findByLabelText('向数据助手提问');
    fireEvent.change(composer, { target: { value: '查看前10条' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /发送$/ })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /发送$/ }));
    await screen.findByText(warning);
    expect(composer).toBeEnabled();
    fireEvent.change(composer, { target: { value: '等待队列恢复时的新草稿' } });
    expect(screen.getByRole('button', { name: /中止$/ })).toBeEnabled();
    queueRecovered = true;
    await waitFor(() => expect(screen.queryByText(warning)).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /中止$/ }));
    await screen.findByText('本次提问已中止，没有执行未确认的操作。');
    expect(composer).toHaveValue('等待队列恢复时的新草稿');
    expect(fetchMock.mock.calls.some(([route, options]) => route === '/api/runs/offline-run/cancel' && options?.method === 'POST')).toBe(true);
  });
  it('服务器隐藏状态同步助手和工作台，恢复只改变显示而不重算快照', async () => {
    window.history.replaceState(null, '', '/conversations/c1');
    let hidden: string[] = [];
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', ''); let payload: unknown;
      if (path === '/auth/me') payload = { id: 'u1', username: '学习账号', csrf_token: 'csrf' };
      else if (path === '/catalog') payload = { tables, tasks: [], recent_operations: [], model_mode: 'disabled', hidden_result_ids: hidden };
      else if (path === '/conversations') payload = { conversations: [{ id: 'c1', title: '快照对话' }] };
      else if (path === '/conversations/c1/runs') payload = { runs: [] };
      else if (path === '/conversations/c1') payload = { id: 'c1', title: '快照对话', messages: [{ id: 'm1', role: 'assistant', content: '查询完成', result_id: fixture.id }], results: [fixture], hidden_result_ids: hidden };
      else if (path === `/results/${fixture.id}/visibility`) { hidden = options.method === 'DELETE' ? [fixture.id] : []; payload = { hidden: hidden.length > 0 }; }
      else throw new Error(`意外请求：${path}`);
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await screen.findByText('查询完成');
    fireEvent.click(screen.getByLabelText(`隐藏结果 ${fixture.id}`));
    await waitFor(() => expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(0));
    fireEvent.click(screen.getByText(/已隐藏结果 \(1\)/));
    fireEvent.click(await screen.findByLabelText(`恢复显示 ${fixture.id}`));
    await waitFor(() => expect(container.querySelectorAll('[data-result-id="result-unique"]')).toHaveLength(2));
    expect(fetchMock.mock.calls.some(([url]) => url.includes('/runs/') || url.includes('/tables/'))).toBe(false);
  });
  it('修改对话名称向服务器保存且使用更新后的名称', async () => {
    let title = '原标题';
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      if (path === '/conversations/c1' && options.method === 'PATCH') title = JSON.parse(String(options.body)).title;
      const payload = path === '/auth/me' ? { id: 'u1', username: '学习账号', csrf_token: 'csrf' }
        : path === '/catalog' ? { tables, tasks: [], recent_operations: [], model_mode: 'disabled' }
        : path === '/conversations' ? { conversations: [{ id: 'c1', title }] }
        : path === '/conversations/c1/runs' ? { runs: [] }
        : { id: 'c1', title, messages: [], results: [] };
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await screen.findByLabelText('向数据助手提问');
    await waitFor(() => expect(screen.getByLabelText('修改对话名称')).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: '修改对话名称' }));
    fireEvent.change(await screen.findByLabelText('对话名称'), { target: { value: '我的成绩分析' } });
    fireEvent.click(screen.getByText('保存名称'));
    await waitFor(() => expect(title).toBe('我的成绩分析'));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '修改对话名称' })).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '修改对话名称' }));
    await waitFor(() => expect(screen.getByLabelText('对话名称')).toHaveValue('我的成绩分析'));
    expect(fetchMock.mock.calls.some(([url, options]) => url === '/api/conversations/c1' && options?.method === 'PATCH')).toBe(true);
  });
  it('首页、表详情和个人页独立路由，浏览器前进后退保留 Agent 草稿', async () => {
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      const profile = { id: 'u1', username: '学习账号', csrf_token: 'csrf', display_name: '昵称', bio: '', email: null, avatar_url: null };
      const payload = path === '/auth/me' || path === '/profile' ? profile
        : path === '/catalog' ? { tables, tasks: [], recent_operations: [], model_mode: 'disabled' }
        : path === '/conversations' ? { conversations: [{ id: 'c1', title: '旧对话名', last_question: '查看成绩' }] }
        : path === '/conversations/c1/runs' ? { runs: [] }
        : { id: 'c1', title: '旧对话名', messages: [{ id: 'user-one', role: 'user', content: '查看成绩' }], results: [] };
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    const composer = await screen.findByLabelText('向数据助手提问');
    fireEvent.change(composer, { target: { value: '切换页面时保留的问题草稿' } });
    expect(screen.getByRole('heading', { level: 1, name: '首页' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 成绩数据' }));
    await screen.findByRole('heading', { level: 1, name: '成绩数据' }); expect(window.location.pathname).toBe('/tables/table1');
    fireEvent.click(screen.getByRole('link', { name: /用户个人主页$/ }));
    await screen.findByRole('heading', { level: 1, name: '用户个人主页' }); expect(window.location.pathname).toBe('/profile');
    expect(screen.getByLabelText('向数据助手提问')).toBe(composer);
    window.history.back(); await screen.findByRole('heading', { level: 1, name: '成绩数据' });
    window.history.back(); await screen.findByRole('heading', { level: 1, name: '首页' });
    window.history.forward(); await screen.findByRole('heading', { level: 1, name: '成绩数据' });
    window.history.forward(); await screen.findByRole('heading', { level: 1, name: '用户个人主页' });
    expect(composer).toHaveValue('切换页面时保留的问题草稿');
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  });
  it('最近对话下拉按接口活动顺序展示最新提问，空对话显示新对话', async () => {
    const conversations = [
      { id: 'c2', title: '第二张表的旧标题', last_question: '最新一次用户提问', updated_at: '2026-10-12T06:00:00Z' },
      { id: 'c1', title: '成绩数据旧标题', last_question: '较早一次用户提问', updated_at: '2026-10-11T06:00:00Z' },
      { id: 'c3', title: '以表名生成的默认标题', last_question: null, updated_at: '2026-10-10T06:00:00Z' },
    ];
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      const item = conversations.find(conversation => path === `/conversations/${conversation.id}`);
      const payload = path === '/auth/me' ? { id: 'u1', username: '学习账号', csrf_token: 'csrf' }
        : path === '/catalog' ? { tables, tasks: [], recent_operations: [], model_mode: 'disabled' }
        : path === '/conversations' ? { conversations }
        : path.endsWith('/runs') ? { runs: [] }
        : item ? { ...item, messages: item.last_question ? [{ id: `${item.id}-user`, role: 'user', content: item.last_question }] : [], results: [] } : {};
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await screen.findByLabelText('向数据助手提问');
    await waitFor(() => expect(localStorage.getItem('vibe:u1:conversation')).toBe('c2'));
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '最近对话' }));
    await screen.findByText('较早一次用户提问', { selector: '.table-option-label' });
    const optionLabels = Array.from(document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .table-option-label')).map(node => node.textContent);
    expect(optionLabels).toEqual(['最新一次用户提问', '较早一次用户提问', '新对话']);
    expect(screen.queryByText('成绩数据旧标题')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('新对话', { selector: '.table-option-label' }));
    await waitFor(() => expect(window.location.pathname).toBe('/conversations/c3'));
    await waitFor(() => expect(localStorage.getItem('vibe:u1:conversation')).toBe('c3'));
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
  });
  it('表详情精准记录预览经统一计划确认执行，确认后刷新记录与最近摘要', async () => {
    window.history.replaceState(null, '', '/tables/table1');
    let confirmed = false;
    const plan: ChangePlan = { id: 'row-plan', status: 'pending', operation: 'update', table_id: 'table1', table_version: 1, affected_count: 1, conversation_id: 'c1', before: [{ id: 'row-two', values: { score: 87 } }], after: [{ id: 'row-two', values: { score: 90 } }], expires_at: '2099-01-01T00:00:00Z' };
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const path = url.replace('/api', '');
      if (path === '/tables/table1?offset=0&limit=20') return new Response(JSON.stringify({ rows: [{ id: 'row-one', values: { score: 87 } }, { id: 'row-two', values: { score: confirmed ? 90 : 87 } }], total: 2 }));
      if (path === '/tables/table1/changes?limit=10') return new Response(JSON.stringify({ operations: confirmed ? [{ id: 'change-one', summary: '更新了 1 条记录' }] : [] }));
      if (path === '/tables/table1/rows/preview') return new Response(JSON.stringify({ plan }));
      if (path === '/plans/row-plan/confirm') { confirmed = true; return new Response(JSON.stringify({ plan: { ...plan, status: 'confirmed' } })); }
      const page = pageRead(url, options); if (page) return page;
      const payload = path === '/auth/me' ? { id: 'u1', username: '学习账号', csrf_token: 'csrf' }
        : path === '/catalog' ? { tables: [{ ...tables[0], row_count: 2, version: confirmed ? 2 : 1 }, tables[1]], tasks: [], recent_operations: [], model_mode: 'disabled' }
        : path === '/conversations' ? { conversations: [{ id: 'c1', title: '记录编辑', last_question: null }] }
        : path === '/conversations/c1/runs' ? { runs: [] }
        : { id: 'c1', title: '记录编辑', messages: [], results: [] };
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    await waitFor(() => {
      const button = screen.queryByRole('button', { name: '编辑记录 row-two' });
      if (!button) throw new Error(`表详情尚未出现：${window.location.pathname}，请求路径：${fetchMock.mock.calls.map(([url]) => url).join(', ')}`);
      expect(button).toBeInTheDocument();
    }, { timeout: 15000 });
    await waitFor(() => expect(localStorage.getItem('vibe:u1:conversation')).toBe('c1'));
    fireEvent.click(screen.getByRole('button', { name: '编辑记录 row-two' }));
    fireEvent.change(screen.getByLabelText('记录字段 成绩'), { target: { value: '90' } });
    fireEvent.click(screen.getByRole('button', { name: '预览记录变更' }));
    // Drawer 和 Modal 在测试环境可能共用标题 ID，按保存的计划 ID 定位唯一确认入口。
    await waitFor(() => expect(document.querySelector('[data-plan-id="row-plan"]')).toBeInTheDocument(), { timeout: 15000 });
    const preview = document.querySelector('[data-plan-id="row-plan"]')!.closest('[role="dialog"]') as HTMLElement;
    expect(confirmed).toBe(false);
    const request = fetchMock.mock.calls.find(([url]) => url.endsWith('/rows/preview'))!;
    expect(JSON.parse(String(request[1]?.body))).toMatchObject({ row_id: 'row-two', version: 1, operation: 'update', values: { score: 90 } });
    fireEvent.click(within(preview).getByRole('button', { name: '确认执行 1 条变更' }));
    await screen.findByText('更新了 1 条记录');
    expect(confirmed).toBe(true);
    const unchanged = screen.getByRole('button', { name: '编辑记录 row-one' }).closest('tr')!;
    const updated = screen.getByRole('button', { name: '编辑记录 row-two' }).closest('tr')!;
    expect(unchanged).toHaveTextContent('87'); expect(updated).toHaveTextContent('90');
    expect(fetchMock.mock.calls.filter(([url, options]) => url.endsWith('/confirm') && options?.method === 'POST')).toHaveLength(1);
  });
  it.each([false, true])('追问保存计划恢复原会话与表范围并保留草稿（联表：%s）', async joined => {
    const savedPlan: ChangePlan = { id: 'saved-plan', conversation_id: 'c1', operation: 'select', table_id: 'table1', table_version: 1,
      status: 'pending', affected_count: 3, before: [], after: joined ? [{ id: 'relation1', values: { left_table_id: 'table1', right_table_id: 'table2' } }] : [],
      expires_at: '2099-10-10T09:00:00Z', intent: '准备查询成绩；确认后执行。', plan: { operation: 'select', table_id: 'table1', fields: ['score'] } };
    let releasePlan!: (value: ChangePlan) => void;
    const planResponse = new Promise<ChangePlan>(resolve => { releasePlan = resolve; });
    const fetchMock = vi.fn(async (url: string, options: RequestInit = {}) => {
      const page = pageRead(url, options); if (page) return page;
      const path = url.replace('/api', '');
      let payload: unknown;
      if (path === '/auth/me') payload = { id: 'u1', username: '学习账号', csrf_token: 'csrf' };
      else if (path === '/catalog') payload = { tables, tasks: [], recent_operations: [], model_mode: 'disabled' };
      else if (path === '/conversations') payload = { conversations: [{ id: 'c1', title: '计划原对话', last_question: '原计划的用户提问' }, { id: 'c2', title: '另一个对话', last_question: '另一个对话的问题' }] };
      else if (path === '/conversations/c1') payload = { id: 'c1', title: '计划原对话', messages: [{ id: 'm1', role: 'assistant', content: savedPlan.intent, plan_id: savedPlan.id }], results: [] };
      else if (path === '/conversations/c2') payload = { id: 'c2', title: '另一个对话', messages: [], results: [] };
      else if (path === '/conversations/c1/runs' || path === '/conversations/c2/runs') payload = options.method === 'POST'
        ? { id: 'modified-run', status: 'queued', conversation_id: path.includes('/c1/') ? 'c1' : 'c2' } : { runs: [] };
      else if (path === '/runs/modified-run') payload = { id: 'modified-run', status: 'cancelled', conversation_id: 'c1' };
      else if (path === `/plans/${savedPlan.id}`) payload = await planResponse;
      else throw new Error(`意外请求：${path}`);
      return new Response(JSON.stringify(payload));
    });
    vi.stubGlobal('fetch', fetchMock);
    const { container } = render(<ConfigProvider><AntApp><App /></AntApp></ConfigProvider>);
    const composer = await screen.findByLabelText('向数据助手提问');
    const draft = '请把最优口径改为最大值，先重新预览';
    fireEvent.change(composer, { target: { value: draft } });
    fireEvent.click(await screen.findByText('查看操作预览与状态'));
    // 模拟预览请求较慢，用户已切换表和对话；响应仍必须关联原计划。
    fireEvent.mouseDown(screen.getByLabelText('全部数据表', { selector: 'input' }));
    fireEvent.click(await screen.findByText('第二张数据含可读完整表名', { selector: '.table-option-label' }));
    fireEvent.mouseDown(screen.getByLabelText('选择对话', { selector: 'input' }));
    fireEvent.click(await screen.findByText('另一个对话的问题', { selector: '.ant-select-item-option-content' }));
    await waitFor(() => expect(localStorage.getItem('vibe:u1:conversation')).toBe('c2'));
    releasePlan(savedPlan);
    await waitFor(() => expect(document.querySelector('[data-plan-id="saved-plan"]')).toBeInTheDocument());
    expect(container.querySelector('[data-result-id]')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('追问修改此计划'));
    await waitFor(() => expect(localStorage.getItem('vibe:u1:conversation')).toBe('c1'));
    await waitFor(() => expect(container.querySelector('.assistant-context .ant-select-selection-item')).toHaveTextContent('成绩数据'));
    expect(composer).toHaveValue(draft);
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false);
    const sendButton = within(composer.closest('.composer') as HTMLElement).getByRole('button', { name: /发送$/ });
    await waitFor(() => expect(sendButton).toBeEnabled());
    fireEvent.click(sendButton);
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, options]) => url === '/api/conversations/c1/runs' && options?.method === 'POST')).toBe(true));
    const submitted = fetchMock.mock.calls.find(([url, options]) => url === '/api/conversations/c1/runs' && options?.method === 'POST');
    expect(JSON.parse(String(submitted?.[1]?.body))).toEqual({ content: draft, table_ids: joined ? ['table1', 'table2'] : ['table1'] });
    expect(fetchMock.mock.calls.some(([url, options]) => url.endsWith('/confirm') || (url === '/api/conversations/c2/runs' && options?.method === 'POST'))).toBe(false);
    expect(container.querySelector('[data-result-id]')).not.toBeInTheDocument();
  });
});
