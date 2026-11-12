/** Vite 开发代理与 Vitest 测试配置，生产请求由 Nginx 代理到同源 API。 */
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: { environment: 'jsdom', setupFiles: ['./src/test-setup.ts'], clearMocks: true, testTimeout: 20000 },
});
