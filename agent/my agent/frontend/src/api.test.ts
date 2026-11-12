/** 验证 API 仅使用同源服务端会话与 CSRF，错误携带真实服务器详情。 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, setCsrfToken } from './api';
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(''); });
describe('服务端 API 客户端', () => {
  it('写入携带 CSRF Cookie，无浏览器身份头或模型密钥', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock); setCsrfToken('verified-csrf');
    await api('/plans/opaque-id/confirm', { method: 'POST' });
    expect(fetchMock.mock.calls[0][0]).toBe('/api/plans/opaque-id/confirm');
    const options = fetchMock.mock.calls[0][1] as RequestInit;
    expect(options.credentials).toBe('same-origin'); expect(options.body).toBeUndefined();
    expect(new Headers(options.headers).get('X-CSRF-Token')).toBe('verified-csrf');
    expect(new Headers(options.headers).get('X-Owner-Id')).toBeNull();
    expect(new Headers(options.headers).get('Authorization')).toBeNull();
  });
  it('显式抛出401/越权和保存错误，而非误报成功', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: '版本冲突，请重新预览' }), { status: 409 })));
    await expect(api('/plans/p1/confirm', { method: 'POST' })).rejects.toEqual(new ApiError('版本冲突，请重新预览', 409));
  });
});
