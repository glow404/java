/** 表级管理与已登记关联：所有结构操作生成服务器预览，之后由统一计划框确认。 */
import { useEffect, useState } from 'react';
import { Alert, Button, Divider, Drawer, Empty, Input, Select, Space, Tag, Typography } from 'antd';
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons';
import { api, errorText, jsonBody } from './api';
import FieldEditor from './FieldEditor';
import type { ChangePlan, DataTable, Field, TableRelation } from './types';

interface Props { mode?: 'create' | 'relations' | 'archived'; tables: DataTable[]; tableId?: string; conversationId?: string; onClose: () => void; onPlan: (plan: ChangePlan) => void; onRelation: (relation: TableRelation) => void }
export default function TableManager({ mode, tables, tableId, conversationId, onClose, onPlan, onRelation }: Props) {
  const [name, setName] = useState(''); const [description, setDescription] = useState('');
  const [fields, setFields] = useState<Field[]>([]); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const [relations, setRelations] = useState<TableRelation[]>([]); const [suggestions, setSuggestions] = useState<TableRelation[]>([]); const [archived, setArchived] = useState<DataTable[]>([]);
  const [leftId, setLeftId] = useState<string>(); const [rightId, setRightId] = useState<string>(); const [leftField, setLeftField] = useState<string>(); const [rightField, setRightField] = useState<string>(); const [joinType, setJoinType] = useState<'inner' | 'left'>('inner');
  const label = (id: string) => tables.find(table => table.id === id)?.name || id;
  const fieldLabel = (id: string, fieldId: string) => tables.find(table => table.id === id)?.fields.find(field => field.id === fieldId)?.name || fieldId;
  useEffect(() => {
    if (!mode) return;
    let stopped = false; setError(''); setLeftId(tableId); setLeftField(undefined); setRightId(undefined); setRightField(undefined);
    if (mode === 'relations') {
      setBusy(true);
      void Promise.all([api<{ relations: TableRelation[] }>('/relations'), api<{ suggestions: TableRelation[] }>(`/relations/candidates${tableId ? `?table_id=${encodeURIComponent(tableId)}` : ''}`)]).then(([saved, candidates]) => {
        if (!stopped) { setRelations(saved.relations); setSuggestions(candidates.suggestions); }
      }).catch(failure => { if (!stopped) setError(errorText(failure)); }).finally(() => { if (!stopped) setBusy(false); });
    } else if (mode === 'archived') {
      setBusy(true); void api<{ tables: DataTable[] }>('/tables?include_archived=true').then(value => { if (!stopped) setArchived(value.tables.filter(table => table.archived)); }).catch(failure => { if (!stopped) setError(errorText(failure)); }).finally(() => { if (!stopped) setBusy(false); });
    }
    return () => { stopped = true; };
  }, [mode, tableId]);
  const preview = async (path: string, body: unknown) => {
    if (!conversationId) { setError('请先选择一个对话，操作计划将保存在该对话中。'); return; }
    setBusy(true); setError('');
    try { const response = await api<{ plan: ChangePlan }>(path, { method: 'POST', body: jsonBody(body) }); onPlan(response.plan); onClose(); }
    catch (failure) { setError(`${errorText(failure)}。表单内容已保留。`); }
    finally { setBusy(false); }
  };
  const relationPreview = (relation: TableRelation) => void preview('/relations/preview', { conversation_id: conversationId, left_table_id: relation.left_table_id, right_table_id: relation.right_table_id, left_field_id: relation.left_field_id, right_field_id: relation.right_field_id, join_type: relation.join_type });
  const tableOptions = tables.filter(table => !table.archived).map(table => ({ value: table.id, label: table.name }));
  return <Drawer open={Boolean(mode)} title={mode === 'create' ? '新建空数据表' : mode === 'relations' ? '表关联与联表查询' : '已归档数据表'} width={900} onClose={onClose} destroyOnHidden={false}>
    <Alert type="info" showIcon message="先预览，再确认" description={mode === 'relations' ? '只通过已确认的字段关联查询；候选关系不会自动生效。姓名不能可靠识别同名者，优先使用学号或订单号等稳定标识。' : '表级操作保存到当前对话。归档保留服务器数据、源文件和操作历史，恢复也需要确认。'} />
    {error && <Alert className="block-gap" type="error" message="操作未完成" description={error} />}
    {mode === 'create' && <><label className="form-label">新表名称<Input aria-label="新表名称" value={name} onChange={event => setName(event.target.value)} /></label><label className="form-label">新表说明<Input.TextArea aria-label="新表说明" value={description} onChange={event => setDescription(event.target.value)} /></label>
      <FieldEditor fields={fields} onChange={setFields} /><Space className="block-gap" wrap><Button icon={<PlusOutlined />} onClick={() => setFields(current => [...current, { id: `f_${crypto.randomUUID().replaceAll('-', '')}`, name: `字段${current.length + 1}`, type: 'string', provenance: 'user', type_provenance: 'user', aliases: [] }])}>添加字段</Button><Button icon={<DeleteOutlined />} disabled={!fields.length} onClick={() => setFields(current => current.slice(0, -1))}>移除末尾字段</Button><Button type="primary" loading={busy} disabled={!name.trim() || !fields.length || fields.some(field => !field.name.trim())} onClick={() => void preview('/tables/preview', { conversation_id: conversationId, name: name.trim(), description, fields })}>预览创建空表</Button></Space></>}
    {mode === 'relations' && <><h3 className="block-gap">已确认关联</h3>{relations.length ? relations.map(relation => <div key={relation.id} className="relation-row" data-relation-id={relation.id}><Typography.Text>{label(relation.left_table_id)} · {fieldLabel(relation.left_table_id, relation.left_field_id)} ↔ {label(relation.right_table_id)} · {fieldLabel(relation.right_table_id, relation.right_field_id)}</Typography.Text><Space wrap><Tag>{relation.join_type === 'left' ? '左连接' : '内连接'}</Tag>{relation.stale && <Tag color="orange">版本已变化，需重新预览确认</Tag>}{relation.archived && <Tag>关联表已归档，请先恢复</Tag>}{relation.status === 'inactive' && <Tag>已停用</Tag>}</Space><Space wrap><Button size="small" disabled={relation.status === 'inactive' || relation.stale || relation.archived} onClick={() => { onRelation(relation); onClose(); }}>选用此关联查询</Button>{relation.stale && !relation.archived && relation.status !== 'inactive' && <Button size="small" loading={busy} onClick={() => relationPreview(relation)}>重新预览关联</Button>}<Button size="small" disabled={relation.status === 'inactive' || relation.archived} loading={busy} onClick={() => void preview(`/relations/${relation.id}/state/preview`, { conversation_id: conversationId, status: 'inactive' })}>预览停用关联</Button></Space></div>) : <Empty description="暂无已确认关联" />}
      <Divider /><h3>可核对的候选关联</h3>{suggestions.length ? suggestions.map((relation, index) => <div key={index} className="relation-row"><Typography.Text>{label(relation.left_table_id)} · {fieldLabel(relation.left_table_id, relation.left_field_id)} ↔ {label(relation.right_table_id)} · {fieldLabel(relation.right_table_id, relation.right_field_id)}</Typography.Text><Typography.Paragraph type="secondary">{relation.reason}</Typography.Paragraph><Button loading={busy} onClick={() => relationPreview(relation)}>预览候选关联</Button></div>) : <Typography.Paragraph type="secondary">当前没有足够可靠的候选，可手工选择字段并核对。</Typography.Paragraph>}
      <Divider /><h3>手工登记关联</h3><div className="relation-form"><label className="form-label">左表<Select aria-label="关联左表" value={leftId} options={tableOptions} onChange={value => { setLeftId(value); setLeftField(undefined); }} /></label><label className="form-label">左字段<Select aria-label="关联左字段" value={leftField} options={(tables.find(table => table.id === leftId)?.fields || []).map(field => ({ value: field.id, label: `${field.name} (${field.type})` }))} onChange={setLeftField} /></label><label className="form-label">右表<Select aria-label="关联右表" value={rightId} options={tableOptions.filter(table => table.value !== leftId)} onChange={value => { setRightId(value); setRightField(undefined); }} /></label><label className="form-label">右字段<Select aria-label="关联右字段" value={rightField} options={(tables.find(table => table.id === rightId)?.fields || []).map(field => ({ value: field.id, label: `${field.name} (${field.type})` }))} onChange={setRightField} /></label></div><Space wrap><Select aria-label="连接方式" value={joinType} onChange={setJoinType} options={[{ value: 'inner', label: '内连接：仅匹配记录' }, { value: 'left', label: '左连接：保留全部左表记录' }]} /><Button type="primary" loading={busy} disabled={!leftId || !rightId || !leftField || !rightField} onClick={() => relationPreview({ left_table_id: leftId!, right_table_id: rightId!, left_field_id: leftField!, right_field_id: rightField!, join_type: joinType })}>预览手工关联</Button></Space></>}
    {mode === 'archived' && (archived.length ? archived.map(table => <div key={table.id} className="relation-row"><Typography.Text>{table.name}</Typography.Text><Tag>{table.row_count} 条记录 · v{table.version}</Tag><Button loading={busy} onClick={() => void preview(`/tables/${table.id}/state/preview`, { conversation_id: conversationId, version: table.version, archived: false })}>预览恢复 {table.name}</Button></div>) : <Empty className="block-gap" description={busy ? '正在加载…' : '暂无已归档数据表'} />)}
  </Drawer>;
}

