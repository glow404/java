/** 验证结果按唯一 ID 留存、图表口径和按账号隔离的可恢复侧栏偏好。 */
import { beforeEach, describe, expect, it } from 'vitest';
import { clampWidth, loadWidth, retainResults, saveWidth, WIDTHS } from './state';
import { safeChart } from './ResultView';
import type { QueryResult } from './types';

const result = (id: string, table_id: string): QueryResult => ({ id, table_id, columns: [], rows: [], explanation: '', query_snapshot: { table_id } });
beforeEach(() => localStorage.clear());
describe('结果快照留存', () => {
  it('跨表多次查询保留全部快照，同 ID 仅保存一个服务器对象', () => {
    const first = result('r1', 'table1'); const second = result('r2', 'table2');
    const registry = retainResults(retainResults({}, [first]), [second, first]);
    expect(Object.keys(registry)).toEqual(['r1', 'r2']);
    expect(registry.r1).toBe(first); expect(registry.r2).toBe(second);
  });
  it('每组独立分母的达标率改为柱图，不生成误导性的组成饼图', () => {
    const normalized = safeChart({ type: 'pie', x: 'class', y: 'rate', unit: '%', numerator: '达标人数', denominator: '各班人数' });
    expect(normalized.chart?.type).toBe('bar'); expect(normalized.notice).toContain('分母');
    expect(safeChart({ type: 'pie', x: 'class', y: 'count' }).chart?.type).toBe('pie');
  });
});
describe('宽度偏好', () => {
  it('限制尺寸并按账号保存，另一账号读取自己的默认值', () => {
    saveWidth('left', 'u1', 5000); expect(loadWidth('left', 'u1')).toBe(WIDTHS.left.max);
    expect(loadWidth('left', 'u2')).toBe(WIDTHS.left.default);
    expect(clampWidth('right', 1)).toBe(WIDTHS.right.min);
  });
  it('无效存储不会破坏布局', () => {
    localStorage.setItem('vibe:u1:width:right', 'invalid'); expect(loadWidth('right', 'u1')).toBe(WIDTHS.right.default);
  });
});
