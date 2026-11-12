/** 独立路由页面验收：目录关系、精准行预览及账号资料流程使用本地 API 替身。 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App as AntApp, ConfigProvider } from 'antd';
import HomePage from './HomePage';
import TablePage from './TablePage';
import ProfilePage from './ProfilePage';
import type { ChangePlan, DataTable, User } from './types';

const tables: DataTable[] = [
  { id: 'table-one', name: '成绩', description: '第一份成绩记录', version: 4, row_count: 21, fields: [{ id: 'student', name: '学生编号', type: 'string' }, { id: 'score', name: '分数', type: 'number' }] },
  { id: 'table-two', name: '姓名', description: '学生姓名登记', version: 1, row_count: 2, fields: [{ id: 'student_id', name: '登记编号', type: 'string' }] },
];
const user: User = { id: 'user-one', username: 'login-account', csrf_token: 'csrf', display_name: '原昵称', bio: '原简介', email: null, avatar_url: null };
const tableLabel = (table: DataTable) => table.name;
const wrapper = ({ children }: { children: React.ReactNode }) => <ConfigProvider><AntApp>{children}</AntApp></ConfigProvider>;
const jsonResponse = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('首页及完整数据表', () => {
  it('首页显示当前账号活动表、说明和登记关联，管理入口只通知父组件', () => {
    const onOpen = vi.fn(); const onCreate = vi.fn(); const onEdit = vi.fn(); const onDelete = vi.fn();
    render(<HomePage tables={[...tables, { ...tables[0], id: 'archived', archived: true, name: '归档表' }]} loading={false} tableLabel={tableLabel} relations={[{ id: 'relation-one', left_table_id: 'table-one', right_table_id: 'table-two', left_field_id: 'student', right_field_id: 'student_id', join_type: 'left', status: 'active', stale: true }]} onOpen={onOpen} onCreate={onCreate} onImport={vi.fn()} onEdit={onEdit} onDelete={onDelete} onRelations={vi.fn()} />, { wrapper });
    expect(screen.getByText('当前账号数据库中的数据表').parentElement).toHaveTextContent('2');
    expect(screen.queryByText('归档表')).not.toBeInTheDocument();
    expect(screen.getByText('第一份成绩记录')).toBeInTheDocument();
    expect(screen.getByText('成绩 · 学生编号 ↔ 姓名 · 登记编号')).toBeInTheDocument();
    expect(screen.getByText('版本变化，需重新确认')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看 成绩' })); expect(onOpen).toHaveBeenCalledWith('table-one');
    fireEvent.click(screen.getByRole('button', { name: '编辑 成绩' })); expect(onEdit).toHaveBeenCalledWith(tables[0]);
    fireEvent.click(screen.getByRole('button', { name: '永久删除 成绩' })); expect(onDelete).toHaveBeenCalledWith(tables[0]);
    fireEvent.click(screen.getByRole('button', { name: /新增数据表/ })); expect(onCreate).toHaveBeenCalledOnce();
  });
  it('分页请求所有记录，重复业务编号的编辑及删除仅预览指定 row_id', async () => {
    const onPlan = vi.fn();
    const plan: ChangePlan = { id: 'preview-one', status: 'pending', operation: 'update', table_id: 'table-one', table_version: 4, affected_count: 1, before: [], after: [], expires_at: '2026-12-01T00:00:00Z' };
    const fetchMock = vi.fn(async (url: string, _options: RequestInit = {}) => {
      if (url.endsWith('/changes?limit=10')) return jsonResponse({ operations: [{ id: 'change-one', summary: '编辑了 1 条成绩记录', created_at: '2026-10-12T04:00:00Z' }] });
      if (url.endsWith('/rows/preview')) return jsonResponse({ plan });
      return jsonResponse({ rows: url.includes('offset=20') ? [{ id: 'last-row', values: { student: 'S99', score: 99 } }] : [{ id: 'first-row', values: { student: 'same-id', score: 10 } }, { id: 'second-row', values: { student: 'same-id', score: 20 } }], total: 21 });
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<TablePage table={tables[0]} tables={tables} tableLabel={tableLabel} conversationId="conversation-one" onSelect={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} onRelations={vi.fn()} onExport={vi.fn()} onPlan={onPlan} />, { wrapper });
    await screen.findByRole('button', { name: '编辑记录 second-row' });
    expect(screen.getByText('编辑了 1 条成绩记录')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '编辑记录 second-row' }));
    const drawer = await screen.findByRole('dialog', { name: '编辑记录' });
    fireEvent.change(within(drawer).getByLabelText('记录字段 分数'), { target: { value: '0' } });
    fireEvent.click(within(drawer).getByRole('button', { name: '预览记录变更' }));
    await waitFor(() => expect(onPlan).toHaveBeenCalledWith(plan));
    let sent = fetchMock.mock.calls.find(([url]) => url.endsWith('/rows/preview'))!;
    expect(JSON.parse(String(sent[1]?.body))).toEqual({ conversation_id: 'conversation-one', version: 4, operation: 'update', row_id: 'second-row', values: { student: 'same-id', score: 0 } });
    expect(screen.getByText('20')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除记录 first-row' }));
    await waitFor(() => expect(onPlan).toHaveBeenCalledTimes(2));
    sent = fetchMock.mock.calls.filter(([url]) => url.endsWith('/rows/preview'))[1];
    expect(JSON.parse(String(sent[1]?.body))).toMatchObject({ row_id: 'first-row', operation: 'delete', values: {} });
    fireEvent.click(screen.getByTitle('2'));
    await screen.findByRole('button', { name: '编辑记录 last-row' });
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/tables/table-one?offset=20&limit=20')).toBe(true);
  });
  it('取消记录编辑不写入，数字错误保留草稿，版本变化不覆盖旧编辑版本', async () => {
    const fetchMock = vi.fn(async (url: string, _options: RequestInit = {}) => url.endsWith('/changes?limit=10') ? jsonResponse({ operations: [] }) : url.endsWith('/rows/preview') ? jsonResponse({ detail: '表版本已变化，请刷新' }, 409) : jsonResponse({ rows: [{ id: 'row-one', values: { student: 'S1', score: 60 } }], total: 1 }));
    vi.stubGlobal('fetch', fetchMock);
    const props = { tables, tableLabel, conversationId: 'conversation-one', onSelect: vi.fn(), onEdit: vi.fn(), onDelete: vi.fn(), onRelations: vi.fn(), onExport: vi.fn(), onPlan: vi.fn() };
    const { rerender } = render(<TablePage table={tables[0]} {...props} />, { wrapper });
    fireEvent.click(await screen.findByRole('button', { name: '编辑记录 row-one' }));
    fireEvent.click(screen.getByRole('button', { name: '取消编辑' }));
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/rows/preview'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '编辑记录 row-one' }));
    fireEvent.change(screen.getByLabelText('记录字段 分数'), { target: { value: 'not-number' } });
    fireEvent.click(screen.getByRole('button', { name: '预览记录变更' }));
    await screen.findByText(/分数 需要填写有效数字/);
    expect(screen.getByLabelText('记录字段 分数')).toHaveValue('not-number');
    fireEvent.change(screen.getByLabelText('记录字段 分数'), { target: { value: '61' } });
    rerender(<TablePage table={{ ...tables[0], version: 5 }} {...props} />);
    fireEvent.click(screen.getByRole('button', { name: '预览记录变更' }));
    await screen.findByText(/表版本已变化，请刷新/);
    const sent = fetchMock.mock.calls.find(([url]) => url.endsWith('/rows/preview'))!;
    expect(JSON.parse(String(sent[1]?.body)).version).toBe(4);
    expect(screen.getByLabelText('记录字段 分数')).toHaveValue('61');
  });
  it('新增空数字用 null 预览，读取和历史失败提供可见错误', async () => {
    const onPlan = vi.fn();
    const fetchMock = vi.fn(async (url: string, _options: RequestInit = {}) => url.endsWith('/rows/preview') ? jsonResponse({ plan: { id: 'new-row-plan' } }) : jsonResponse({ detail: '临时读取失败' }, 503));
    vi.stubGlobal('fetch', fetchMock);
    render(<TablePage table={tables[0]} tables={tables} tableLabel={tableLabel} conversationId="conversation-one" onSelect={vi.fn()} onEdit={vi.fn()} onDelete={vi.fn()} onRelations={vi.fn()} onExport={vi.fn()} onPlan={onPlan} />, { wrapper });
    await screen.findByText('读取或操作未完成'); expect(screen.getByText('变更记录读取失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /新增记录/ }));
    fireEvent.change(screen.getByLabelText('记录字段 学生编号'), { target: { value: 'S2' } });
    fireEvent.click(screen.getByRole('button', { name: '预览记录变更' }));
    await waitFor(() => expect(onPlan).toHaveBeenCalledWith({ id: 'new-row-plan' }));
    const sent = fetchMock.mock.calls.find(([url]) => url.endsWith('/rows/preview'))!;
    expect(JSON.parse(String(sent[1]?.body))).toEqual({ conversation_id: 'conversation-one', version: 4, operation: 'insert', values: { student: 'S2', score: null } });
  });
});

describe('个人主页', () => {
  it('昵称不改变登录账号，邮箱变更先验证当前密码并保存安全资料', async () => {
    const onUpdated = vi.fn();
    const fetchMock = vi.fn(async (_url: string, options: RequestInit = {}) => options.method === 'PATCH' ? jsonResponse({ ...user, display_name: '新昵称', email: 'user@example.com' }) : jsonResponse(user));
    vi.stubGlobal('fetch', fetchMock);
    render(<ProfilePage user={user} onUpdated={onUpdated} onPasswordChanged={vi.fn()} />, { wrapper });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/profile', expect.anything()));
    fireEvent.change(screen.getByLabelText('显示昵称'), { target: { value: '新昵称' } });
    fireEvent.change(screen.getByLabelText('绑定邮箱'), { target: { value: 'user@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: '保存个人资料' }));
    await screen.findByText('绑定、修改或解绑邮箱需要验证当前密码。');
    expect(fetchMock.mock.calls.some(([, options]) => options?.method === 'PATCH')).toBe(false);
    fireEvent.change(screen.getByLabelText('邮箱变更验证密码'), { target: { value: 'old-password-for-test' } });
    fireEvent.click(screen.getByRole('button', { name: '保存个人资料' }));
    await waitFor(() => expect(onUpdated).toHaveBeenCalledOnce());
    const sent = fetchMock.mock.calls.find(([, options]) => options?.method === 'PATCH')!;
    expect(JSON.parse(String(sent[1]?.body))).toEqual({ display_name: '新昵称', bio: '原简介', email: 'user@example.com', current_password: 'old-password-for-test' });
    expect(screen.getByLabelText('登录用户名')).toHaveValue('login-account'); expect(screen.getByLabelText('登录用户名')).toBeDisabled();
    expect(screen.queryByLabelText('邮箱变更验证密码')).not.toBeInTheDocument();
  });
  it('密码不足12字符不提交，成功改密后通知父组件全部重新登录', async () => {
    const onPasswordChanged = vi.fn();
    const fetchMock = vi.fn(async (url: string) => jsonResponse(url.endsWith('/password') ? { status: 'ok' } : user));
    vi.stubGlobal('fetch', fetchMock);
    render(<ProfilePage user={user} onUpdated={vi.fn()} onPasswordChanged={onPasswordChanged} />, { wrapper });
    fireEvent.change(screen.getByLabelText('修改密码的当前密码'), { target: { value: 'old-password-for-test' } });
    fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'short' } });
    fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'short' } });
    fireEvent.click(screen.getByRole('button', { name: '修改密码并重新登录' }));
    await screen.findByText('新密码至少需要 12 个字符。');
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/password'))).toBe(false);
    fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'new-password-for-test' } });
    fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'new-password-for-test' } });
    fireEvent.click(screen.getByRole('button', { name: '修改密码并重新登录' }));
    await waitFor(() => expect(onPasswordChanged).toHaveBeenCalledOnce());
    expect(screen.getByLabelText('新密码')).toHaveValue('');
  });
  it('头像类型与大小前置校验，合法文件按 multipart 上传并使用返回头像地址', async () => {
    const onUpdated = vi.fn();
    const fetchMock = vi.fn(async (url: string, _options: RequestInit = {}) => jsonResponse(url.endsWith('/avatar') ? { ...user, avatar_url: '/api/profile/avatar?v=2' } : user));
    vi.stubGlobal('fetch', fetchMock);
    render(<ProfilePage user={user} onUpdated={onUpdated} onPasswordChanged={vi.fn()} />, { wrapper });
    const fileInput = screen.getByLabelText('上传头像');
    fireEvent.change(fileInput, { target: { files: [new File(['text'], 'invalid.svg', { type: 'image/svg+xml' })] } });
    await screen.findByText('请选择不超过 2 MB 的 JPG、PNG 或 WebP 图片。');
    expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/avatar'))).toBe(false);
    const validFile = new File(['fake-png-for-mock'], 'avatar.png', { type: 'image/png' });
    fireEvent.change(fileInput, { target: { files: [validFile] } });
    await waitFor(() => expect(onUpdated).toHaveBeenCalledOnce());
    const sent = fetchMock.mock.calls.find(([url]) => url.endsWith('/avatar'))!;
    expect(sent[1]?.body).toBeInstanceOf(FormData); expect((sent[1]?.body as FormData).get('file')).toBe(validFile);
    expect(document.querySelector('img')).toHaveAttribute('src', '/api/profile/avatar?v=2');
  });
});
