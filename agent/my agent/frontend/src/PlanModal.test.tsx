/** 预览是确认门槛：查询无提前结果，元数据前后值可审阅，旧计划不能确认。 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import PlanModal from './PlanModal';
import type { ChangePlan } from './types';

afterEach(cleanup);
const preview: ChangePlan = { id: 'plan1', operation: 'select', table_id: 'table1', table_version: 3, status: 'pending', affected_count: 30, before: [], after: [], expires_at: '2099-10-10T09:00:00Z', intent: '查询分数大于80的学生；确认后执行，30是基表记录数。', plan: { operation: 'select', filters: [{ field_id: 'score', op: 'gt', value: 80 }] } };
describe('统一操作预览', () => {
  it('查询说明实际范围且只在明确确认时回调，不生成本地结果', () => {
    const onConfirm = vi.fn(); const onCancel = vi.fn(); const onFollowUp = vi.fn();
    render(<PlanModal plan={preview} busy={false} error="" onClose={() => {}} onConfirm={onConfirm} onCancel={onCancel} onFollowUp={onFollowUp} />);
    expect(screen.getByText(preview.intent!)).toBeInTheDocument();
    expect(document.querySelector('.snapshot-id')).toHaveTextContent('计划 plan1 · 表 table1 · 表 ID table1');
    expect(document.querySelector('[data-plan-operation="select"]')).toBeInTheDocument();
    expect(document.querySelector('[data-result-id]')).not.toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('追问修改此计划')); expect(onFollowUp).toHaveBeenCalledOnce(); expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('取消此计划')); expect(onCancel).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByText('确认执行查询')); expect(onConfirm).toHaveBeenCalledOnce();
  });
  it('显示元数据改动前后说明，并阻止已取消计划继续执行', () => {
    const changed: ChangePlan = { ...preview, operation: 'metadata', status: 'cancelled', before: [{ id: 'table1', values: { description: '文件原文说明' } }], after: [{ id: 'table1', values: { description: '用户更新说明' } }] };
    render(<PlanModal plan={changed} busy={false} error="" onClose={() => {}} onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.getByText(/文件原文说明/)).toBeInTheDocument();
    expect(screen.getByText(/用户更新说明/)).toBeInTheDocument();
    expect(screen.getByText('确认执行修改表与字段说明').closest('button')).toBeDisabled();
  });
  it('将模糊指标的选择、依据和替代项作为待确认建议展示，追问不执行查询', () => {
    const proposed: ChangePlan = { ...preview, plan: { operation: 'select', assumptions: [
      { subject: 'FRR 条件', choice: '按小数比例筛选 frr < 0.03', reason: '字段说明标记为比例，3% 换算为 0.03。', alternatives: ['按百分数存储筛选 frr < 3'] },
      { subject: '最优 FAR', choice: '取 far 的最小值，并返回 unlock_threshold', reason: 'FAR 表示错误接受率，越低越好。', alternatives: ['将对应阈值改为 match_score_threshold'] },
    ] } };
    const onConfirm = vi.fn(); const onFollowUp = vi.fn();
    render(<PlanModal plan={proposed} busy={false} error="" onClose={() => {}} onConfirm={onConfirm} onCancel={() => {}} onFollowUp={onFollowUp} />);
    expect(screen.getByText('建议口径（待确认）')).toBeInTheDocument();
    expect(screen.getByText('按小数比例筛选 frr < 0.03')).toBeInTheDocument();
    expect(screen.getByText('判断依据：字段说明标记为比例，3% 换算为 0.03。')).toBeInTheDocument();
    expect(screen.getByText('可修改为：将对应阈值改为 match_score_threshold')).toBeInTheDocument();
    expect(screen.getByText(/并非已确认事实/)).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('追问修改此计划'));
    expect(onFollowUp).toHaveBeenCalledOnce(); expect(onConfirm).not.toHaveBeenCalled();
  });
  it('旧计划或异常建议不崩溃，已执行计划中的建议不再标记待确认', () => {
    const completed: ChangePlan = { ...preview, status: 'confirmed', query_snapshot: { assumptions: [
      { subject: 'FRR 单位', choice: '小数比例', reason: '用户已明确说明。', alternatives: [] },
      { subject: '错误旧值', choice: 3, alternatives: ['忽略'] },
    ] } };
    render(<PlanModal plan={completed} busy={false} error="" onClose={() => {}} onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.getByText('本计划的建议口径')).toBeInTheDocument();
    expect(screen.queryByText('建议口径（待确认）')).not.toBeInTheDocument();
    expect(screen.getByText('小数比例')).toBeInTheDocument();
    expect(screen.getByText('确认执行查询').closest('button')).toBeDisabled();
  });
  it('顶部保留下一步摘要，完整建议依据只在独立建议区展示一次', () => {
    const reason = '字段说明明确为小数比例，3% 应换算为 0.03。';
    const proposed: ChangePlan = { ...preview, intent: `准备筛选 FRR 并返回最小 FAR。\n建议口径（待确认）：\nFRR 单位：小数比例。判断依据：${reason}`, plan: { operation: 'select', assumptions: [{ subject: 'FRR 单位', choice: '小数比例', reason, alternatives: [] }] } };
    render(<PlanModal plan={proposed} busy={false} error="" onClose={() => {}} onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.getByText('准备筛选 FRR 并返回最小 FAR。')).toBeInTheDocument();
    expect(document.querySelector('[data-plan-id="plan1"]')).not.toHaveTextContent(reason);
    expect([...document.querySelectorAll('.ant-alert-description')].filter(item => item.textContent?.includes(reason))).toHaveLength(1);
    expect(screen.getByText('建议口径（待确认）')).toBeInTheDocument();
  });
  it('最优值查询显示已换算筛选值、字段名、并列记录及本次单位范围', () => {
    const optimal: ChangePlan = { ...preview, plan: { operation: 'select', filters: [{ field_id: 'c4', op: 'lt', value: 0.03, storage_unit: 'ratio' }], fields: ['c5', 'c2'], extremum: { field_id: 'c5', direction: 'min' } } };
    render(<PlanModal plan={optimal} table={{ id: 'table1', name: '识别评估', description: '', version: 3, row_count: 30, fields: [{ id: 'c4', name: 'FRR', type: 'number' }, { id: 'c5', name: 'FAR', type: 'number' }, { id: 'c2', name: 'unlock_threshold', type: 'number' }] }} busy={false} error="" onClose={() => {}} onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.getByText('先筛选：FRR（c4） < 0.03（小数比例）。')).toBeInTheDocument();
    expect(screen.getByText(/在筛选范围内取 FAR（c5） 的最小值，返回全部并列最优记录/)).toBeInTheDocument();
    expect(screen.getByText('返回字段：FAR（c5）、unlock_threshold（c2）。')).toBeInTheDocument();
    expect(screen.getByText('存储单位建议仅用于本次查询的数值换算，不会修改登记字段的单位。')).toBeInTheDocument();
    expect(document.querySelector('[data-result-id]')).not.toBeInTheDocument();
  });
  it('百分数占比阈值显示分母，仍沿用服务器换算后的数值', () => {
    const ratioPlan: ChangePlan = { ...preview, plan: { operation: 'select', ratio: { field_id: 'frr', threshold: 3, storage_unit: 'percent', denominator: 'non_null' } } };
    render(<PlanModal plan={ratioPlan} busy={false} error="" onClose={() => {}} onConfirm={() => {}} onCancel={() => {}} />);
    expect(screen.getByText('占比阈值：frr 使用 3（百分数）；分母为该字段非空记录。')).toBeInTheDocument();
  });
  it('联表查询预览显示固定双端版本、字段、基数与空缺重复键，尚未执行查询', () => {
    const joined: ChangePlan = { ...preview, after: [{ id: 'relation1', values: { id: 'relation1', left_table_id: 'students', right_table_id: 'scores', left_name: '学籍表', right_name: '考试明细', left_field_id: 'student_id', right_field_id: 'student_id', left_field: '学号', right_field: '学号', left_version: 3, right_version: 7, join_type: 'left', statistics: { cardinality: 'one_to_many', joined_rows: 6, left: { rows: 4, null_or_empty_rows: 1, duplicate_keys: 0 }, right: { rows: 5, null_or_empty_rows: 0, duplicate_keys: 2 }, unmatched_left_rows: 2, unmatched_right_rows: 1, warnings: ['重复连接键会扩大连接结果，不能重复求和学生汇总值。'] } } }] };
    const onConfirm = vi.fn();
    render(<PlanModal plan={joined} table={{ id: 'students', name: '学籍表（目录新版本）', version: 99, row_count: 100, description: '', fields: [{ id: 'student_id', name: '不应覆盖快照的字段名', type: 'string' }] }} busy={false} error="" onClose={() => {}} onConfirm={onConfirm} onCancel={() => {}} />);
    expect(screen.getByText('本次联表查询的固定连接范围')).toBeInTheDocument();
    expect(screen.getByText('学籍表')).toBeInTheDocument(); expect(screen.getByText('考试明细')).toBeInTheDocument();
    expect(screen.getByText('v3')).toBeInTheDocument(); expect(screen.getByText('v7')).toBeInTheDocument();
    expect(screen.getAllByText('学号')).toHaveLength(2);
    expect(screen.queryByText('不应覆盖快照的字段名')).not.toBeInTheDocument(); expect(screen.queryByText('v99')).not.toBeInTheDocument();
    expect(screen.getByText('左连接：保留全部左表记录')).toBeInTheDocument();
    expect(screen.getByText('连接基数：一对多')).toBeInTheDocument(); expect(screen.getByText('连接记录数：6 条')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '空缺连接键' })).toBeInTheDocument(); expect(screen.getByRole('columnheader', { name: '重复键数' })).toBeInTheDocument();
    expect(screen.getByText('重复连接键会扩大连接结果，不能重复求和学生汇总值。')).toBeInTheDocument();
    expect(screen.getByText(/不代表本次问题筛选后的结果数/)).toBeInTheDocument();
    expect(onConfirm).not.toHaveBeenCalled(); expect(document.querySelector('[data-result-id]')).not.toBeInTheDocument();
  });
});
