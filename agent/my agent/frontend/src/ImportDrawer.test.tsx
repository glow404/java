/** 导入核对替身测试：显式核对、前导零与失败草稿保护。 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App as AntApp } from 'antd';
import ImportDrawer from './ImportDrawer';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
describe('导入确认', () => {
  it('必须人工核对，失败保留修正和原版本供重试', async () => {
    const requests: { url: string; body?: string }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, options: RequestInit = {}) => {
      requests.push({ url, body: options.body as string | undefined });
      if (url.endsWith('/confirm')) return new Response(JSON.stringify({ detail: '数据库暂不可用' }), { status: 503 });
      const body = url.includes('/tasks/') ? { id: 'task1', status: 'completed', kind: 'parse', attempts: 1 }
        : { task_id: 'task1', version: 3, warnings: [], tables: [{ candidate_id: 'sheet1', name: '明细', description: '', fields: [{ id: 'student', name: '学号', type: 'string', provenance: 'file' }], rows: [{ values: { student: '001' }, source: { row: 2 } }], original: { rows: [['学号'], ['001']], row_count: 1 }, warnings: ['空值保持为空'], requires_review: true, row_count: 1 }] };
      return new Response(JSON.stringify(body), { status: 200 });
    }));
    render(<AntApp><ImportDrawer open taskId="task1" onClose={vi.fn()} onImported={vi.fn()} /></AntApp>);
    const student = await screen.findByRole('textbox', { name: '候选行 1 学号' });
    expect(student).toHaveValue('001');
    fireEvent.click(screen.getByRole('button', { name: '确认选中表入库' }));
    await screen.findByText(/请至少选择一张候选表/);
    expect(requests.some(request => request.url.endsWith('/confirm'))).toBe(false);
    fireEvent.change(student, { target: { value: '0007' } });
    fireEvent.click(screen.getByRole('checkbox', { name: /我已核对/ }));
    fireEvent.click(screen.getByRole('button', { name: '确认选中表入库' }));
    await screen.findByText(/数据库暂不可用/);
    expect(student).toHaveValue('0007');
    await waitFor(() => expect(requests.some(request => request.url.endsWith('/confirm'))).toBe(true));
    const body = JSON.parse(requests.find(request => request.url.endsWith('/confirm'))!.body!);
    expect(body.version).toBe(3); expect(body.tables[0].reviewed).toBe(true);
    expect(body.tables[0].corrections).toEqual([{ row_index: 0, field_id: 'student', value: '0007' }]);
  });
});
