/** 唯一变更计划的影响预览；确认与取消仅提交服务端保存的计划 ID。 */
import { Alert, Button, Collapse, Modal, Space, Table, Tag, Typography } from 'antd';
import type { ChangePlan, DataRow, DataTable } from './types';
import { printable } from './ResultView';
import AssumptionPreview, { planAssumptions } from './AssumptionPreview';

const recordValue = (value: unknown): Record<string, unknown> => value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
function QueryStepsPreview({ plan, table }: { plan: ChangePlan; table?: DataTable }) {
  const query = recordValue(plan.plan || plan.query_snapshot);
  const extremum = recordValue(query.extremum);
  const optimal = typeof extremum.field_id === 'string' && ['min', 'max'].includes(String(extremum.direction));
  const filters = (Array.isArray(query.filters) ? query.filters : []).map(recordValue).filter(item => typeof item.field_id === 'string' && typeof item.op === 'string');
  const ratio = recordValue(query.ratio);
  const selectedFields = Array.isArray(query.fields) ? query.fields.filter((item): item is string => typeof item === 'string') : [];
  const ratioUnit = typeof ratio.field_id === 'string' && ['ratio', 'percent'].includes(String(ratio.storage_unit));
  const hasStorageUnit = ratioUnit || filters.some(item => ['ratio', 'percent'].includes(String(item.storage_unit)));
  const name = (id: string) => {
    const field = table?.fields.find(item => item.id === id);
    return field ? `${field.name}（${id}）` : id;
  };
  const units: Record<string, string> = { ratio: '小数比例', percent: '百分数' };
  const operators: Record<string, string> = { eq: '=', ne: '≠', lt: '<', lte: '≤', gt: '>', gte: '≥', in: '属于', contains: '包含', is_null: '为空', not_null: '非空' };
  if (!optimal && !hasStorageUnit) return null;
  return <section className="block-gap">
    <h4>本次查询步骤</h4>
    <ol className="assumption-list">
      {filters.length > 0 && <li>先筛选：{filters.map(item => `${name(String(item.field_id))} ${operators[String(item.op)] || item.op} ${printable(item.value)}${item.unit ? `（${String(item.unit)}）` : units[String(item.storage_unit)] ? `（${units[String(item.storage_unit)]}）` : ''}`).join('；')}。</li>}
      {ratioUnit && <li>占比阈值：{name(String(ratio.field_id))} 使用 {printable(ratio.threshold)}（{String(ratio.unit || units[String(ratio.storage_unit)])}）；分母为{ratio.denominator === 'all' ? '全部记录' : '该字段非空记录'}。</li>}
      {optimal && <li>在筛选范围内取 {name(String(extremum.field_id))} 的{extremum.direction === 'min' ? '最小值' : '最大值'}，返回全部并列最优记录及所选字段；结果仍按服务器分页与数量上限返回。</li>}
      {selectedFields.length > 0 && <li>返回字段：{selectedFields.map(name).join('、')}。</li>}
    </ol>
    {hasStorageUnit && <Typography.Paragraph className="block-gap" type="secondary">存储单位建议仅用于本次查询的数值换算，不会修改登记字段的单位。</Typography.Paragraph>}
  </section>;
}
function RelationScopePreview({ snapshot }: { snapshot: DataRow }) {
  const context = snapshot.values;
  const statistics = recordValue(context.statistics);
  const cardinality = ({ one_to_one: '一对一', one_to_many: '一对多', many_to_one: '多对一', many_to_many: '多对多' } as Record<string, string>)[String(statistics.cardinality)] || '待核对';
  const warnings = Array.isArray(statistics.warnings) ? statistics.warnings.filter((item): item is string => typeof item === 'string') : [];
  const sides = (['left', 'right'] as const).map(side => {
    const profile = recordValue(statistics[side]);
    return { side, table_name: context[`${side}_name`] ?? context[`${side}_table_id`], version: context[`${side}_version`], field: context[`${side}_field`] ?? context[`${side}_field_id`], rows: profile.rows, nulls: profile.null_or_empty_rows, duplicates: profile.duplicate_keys, unmatched: statistics[`unmatched_${side}_rows`] };
  });
  // 这里展示服务端冻结的双表快照，不能用当前目录的版本替换已核对的版本。
  return <section className="block-gap" data-relation-scope-id={snapshot.id}>
    <h4>本次联表查询的固定连接范围</h4>
    <Space wrap><Tag color="cyan">{context.join_type === 'left' ? '左连接：保留全部左表记录' : '内连接：仅保留匹配记录'}</Tag><Tag>连接基数：{cardinality}</Tag><Tag>连接记录数：{printable(statistics.joined_rows)} 条</Tag></Space>
    <Typography.Paragraph className="block-gap" type="secondary">连接检查按两表全部登记记录计算，不代表本次问题筛选后的结果数。确认时会重新核对以下双端版本、连接字段与范围。</Typography.Paragraph>
    <Table rowKey="side" size="small" dataSource={sides} pagination={false} scroll={{ x: 690 }} columns={[
      { title: '数据表', dataIndex: 'table_name', render: printable }, { title: '位置', dataIndex: 'side', render: value => value === 'left' ? '左表' : '右表', width: 70 },
      { title: '固定版本', dataIndex: 'version', render: value => `v${printable(value)}`, width: 85 }, { title: '连接字段', dataIndex: 'field', render: printable },
      { title: '原表记录', dataIndex: 'rows', render: printable }, { title: '空缺连接键', dataIndex: 'nulls', render: printable },
      { title: '重复键数', dataIndex: 'duplicates', render: printable }, { title: '未匹配记录', dataIndex: 'unmatched', render: printable },
    ]} />
    {warnings.length > 0 && <Alert className="block-gap" showIcon type="warning" message="连接口径与重复统计提醒" description={<ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>} />}
    <Collapse className="block-gap" items={[{ key: 'fixed-context', label: '完整连接快照（只读）', children: <pre className="json-preview">{JSON.stringify(context, null, 2)}</pre> }]} />
  </section>;
}

export default function PlanModal({ plan, table, busy, error, onClose, onConfirm, onCancel, onFollowUp }: {
  plan?: ChangePlan; table?: DataTable; busy: boolean; error: string; onClose: () => void;
  onConfirm: () => void; onCancel: () => void;
  onFollowUp?: () => void;
}) {
  const keys = [...new Set([...(table?.fields.map(f => f.id) || []), ...(plan?.before || []).flatMap(row => Object.keys(row.values || {})), ...(plan?.after || []).flatMap(row => Object.keys(row.values || {}))])];
  const columns = [{ title: '记录 ID', dataIndex: 'id', width: 160 }, ...keys.map(id => ({ title: table?.fields.find(f => f.id === id)?.name || id, key: id, render: (_: unknown, row: DataRow) => printable(row.values[id]) }))];
  const pending = plan?.status === 'pending' || plan?.status === 'previewed';
  const labels = { select: '查询', insert: '新增记录', update: '修改记录', delete: '删除记录', metadata: '修改表与字段说明', relation: '建立或调整关联', create_table: '新建数据表', archive_table: '归档数据表', restore_table: '恢复数据表', delete_table: '永久删除数据表' };
  const rowOperation = plan && ['insert', 'update', 'delete'].includes(plan.operation);
  // 助手历史保存完整意图；弹窗已有独立建议区，顶部只保留摘要以避免重复阅读。
  const intentSummary = plan?.intent?.split('\n建议口径（待确认）：')[0];
  return <Modal open={Boolean(plan)} title="操作计划与影响预览" onCancel={onClose} width={940} footer={<Space wrap><Button onClick={onClose}>关闭</Button>{onFollowUp && plan?.operation !== 'delete_table' && <Button disabled={!pending} onClick={onFollowUp}>追问修改此计划</Button>}<Button loading={busy} disabled={!pending} onClick={onCancel}>取消此计划</Button><Button type="primary" danger={['delete', 'archive_table', 'delete_table'].includes(plan?.operation || '')} loading={busy} disabled={!pending} onClick={onConfirm}>确认执行{rowOperation ? ` ${plan?.affected_count || 0} 条变更` : labels[plan?.operation || 'select']}</Button></Space>}>
    {plan && <>
      <div data-plan-id={plan.id} data-plan-operation={plan.operation}><Alert type="warning" showIcon message={`下一步：${labels[plan.operation]}${rowOperation ? ` ${plan.affected_count} 条记录` : ''}`} description={intentSummary || '确认后才执行。服务器会重新检查账号、表版本、有效期和影响范围；追问修改或取消均不会执行旧计划。'} /></div>
      <div className="block-gap"><Tag>{plan.operation === 'create_table' ? '新表待创建' : `表版本 ${plan.table_version}`}</Tag><Tag color={pending ? 'orange' : 'default'}>{plan.status}</Tag><Typography.Text type="secondary">有效期至 {new Date(plan.expires_at).toLocaleString('zh-CN')}</Typography.Text></div>
      {/* 展示保存计划的表 ID，避免同名表或切换工作区后确认了另一份数据。 */}
      <Typography.Paragraph className="snapshot-id">计划 {plan.id} · 表 {table?.name || plan.table_id || (plan.operation === 'delete_table' ? '已永久删除' : '确认后创建')}{plan.table_id && <> · 表 ID {plan.table_id}</>}</Typography.Paragraph>
      {plan.operation === 'delete_table' && <Alert className="block-gap" type="error" showIcon message="永久删除无法恢复" description="请核对下方表编号及清理范围。确认后删除表、记录、关联及相关结果快照；对话文字与不含原始数据的审计保留。" />}
      <AssumptionPreview assumptions={planAssumptions(plan)} pending={pending} />
      {plan.operation === 'select' && <QueryStepsPreview plan={plan} table={table} />}
      {error && <Alert type="error" showIcon message="变更未执行" description={error} className="block-gap" />}
      {plan.operation === 'select' && plan.after?.map((snapshot, index) => <RelationScopePreview key={snapshot.id || index} snapshot={snapshot} />)}
      {(plan.query_snapshot || plan.plan) && <><h4>已校验的操作范围与口径</h4><pre className="json-preview">{JSON.stringify(plan.query_snapshot || plan.plan, null, 2)}</pre></>}
      {rowOperation && plan.operation !== 'insert' && <><h4>{plan.operation === 'delete' ? '删除前快照' : '修改前'}</h4><Table rowKey="id" size="small" columns={columns} dataSource={plan.before} pagination={{ pageSize: 5, showSizeChanger: false }} scroll={{ x: 'max-content' }} /></>}
      {rowOperation && plan.operation !== 'delete' && <><h4>{plan.operation === 'insert' ? '待新增记录' : '修改后'}</h4><Table rowKey="id" size="small" columns={columns} dataSource={plan.after} pagination={{ pageSize: 5, showSizeChanger: false }} scroll={{ x: 'max-content' }} /></>}
      {!rowOperation && plan.operation !== 'select' && <div className="metadata-plan-diff"><section><h4>操作前</h4><pre className="json-preview">{JSON.stringify(plan.before, null, 2)}</pre></section><section><h4>确认后</h4><pre className="json-preview">{JSON.stringify(plan.after, null, 2)}</pre></section></div>}
    </>}
  </Modal>;
}
