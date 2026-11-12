/** 数据表详情页：分页读取完整记录，并将逐行变更交给统一预览确认流程。 */
import { useEffect, useState } from 'react';
import { Alert, Button, Card, Drawer, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd';
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons';
import { api, errorText, jsonBody } from './api';
import type { ChangePlan, DataRow, DataTable, Field, Operation } from './types';

interface Props {
  table?: DataTable; tables: DataTable[]; tableLabel: (table: DataTable) => string; conversationId?: string;
  onSelect: (tableId: string) => void; onEdit: (table: DataTable) => void; onDelete: (table: DataTable) => void;
  onRelations: () => void; onExport: () => void; onPlan: (plan: ChangePlan) => void;
}
const cellText = (value: unknown) => value === null || value === undefined ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value);
function fieldValue(field: Field, value: unknown) {
  if (field.type !== 'number' && field.type !== 'integer') return value === undefined ? null : value;
  if (value === '' || value === null || value === undefined) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || (field.type === 'integer' && !Number.isInteger(numeric))) throw new Error(`${field.name} 需要填写${field.type === 'integer' ? '整数' : '有效数字'}。`);
  return numeric;
}

export default function TablePage({ table, tables, tableLabel, conversationId, onSelect, onEdit, onDelete, onRelations, onExport, onPlan }: Props) {
  const [pagination, setPagination] = useState({ tableId: '', page: 1 }); const [reload, setReload] = useState(0);
  const [rows, setRows] = useState<DataRow[]>([]); const [total, setTotal] = useState(0); const [loading, setLoading] = useState(false); const [error, setError] = useState('');
  const [changes, setChanges] = useState<Operation[]>([]); const [historyError, setHistoryError] = useState('');
  const [editing, setEditing] = useState<{ row?: DataRow; version: number }>(); const [values, setValues] = useState<Record<string, unknown>>({}); const [editorError, setEditorError] = useState(''); const [busy, setBusy] = useState(false);
  const page = pagination.tableId === table?.id ? pagination.page : 1;
  useEffect(() => { setEditing(undefined); setEditorError(''); }, [table?.id]);
  useEffect(() => {
    if (!table) { setRows([]); setTotal(0); setChanges([]); return; }
    let stopped = false; setLoading(true); setRows([]); setError('');
    // 取消标记避免快速切表时，旧请求覆盖新表页面；分页只请求当前需要的 20 条。
    void api<{ rows: DataRow[]; total: number }>(`/tables/${encodeURIComponent(table.id)}?offset=${(page - 1) * 20}&limit=20`).then(result => {
      if (!stopped) { setRows(result.rows); setTotal(result.total); }
    }).catch(failure => { if (!stopped) setError(errorText(failure)); }).finally(() => { if (!stopped) setLoading(false); });
    return () => { stopped = true; };
  }, [table?.id, table?.version, page, reload]);
  useEffect(() => {
    if (!table) return;
    let stopped = false; setChanges([]); setHistoryError('');
    void api<{ operations: Operation[] }>(`/tables/${encodeURIComponent(table.id)}/changes?limit=10`).then(result => { if (!stopped) setChanges(result.operations); }).catch(failure => { if (!stopped) setHistoryError(errorText(failure)); });
    return () => { stopped = true; };
  }, [table?.id, table?.version, reload]);
  const openEditor = (row?: DataRow) => {
    if (!table) return;
    setEditorError(''); setValues(Object.fromEntries(table.fields.map(field => [field.id, row ? row.values[field.id] ?? (field.type === 'boolean' ? null : '') : field.type === 'boolean' ? null : '']))); setEditing({ row, version: table.version });
  };
  const preview = async (operation: 'insert' | 'update' | 'delete', row?: DataRow) => {
    const setFailure = operation === 'delete' ? setError : setEditorError;
    if (!table || !conversationId) { setFailure('请先选择或创建对话，变更预览将保存在该对话中。'); return; }
    if (operation !== 'insert' && !row?.id) { setFailure('该记录缺少行标识，请刷新后重试。'); return; }
    setBusy(true); setFailure('');
    try {
      // 字段名只用于展示；后端按字段 ID 和唯一 row_id 修改，避免同名或重复业务键误改多行。
      const body = { conversation_id: conversationId, version: operation === 'delete' ? table.version : editing?.version ?? table.version, operation, row_id: row?.id, values: operation === 'delete' ? {} : Object.fromEntries(table.fields.map(field => [field.id, fieldValue(field, values[field.id])])) };
      const response = await api<{ plan: ChangePlan }>(`/tables/${encodeURIComponent(table.id)}/rows/preview`, { method: 'POST', body: jsonBody(body) });
      onPlan(response.plan); setEditing(undefined);
    } catch (failure) { setFailure(`${errorText(failure)} 表单及原数据未修改。`); }
    finally { setBusy(false); }
  };
  return <section className="table-page" aria-label="全部数据表">
    <div className="workbench-heading"><div><h1>{table ? tableLabel(table) : '全部数据表'}</h1><p>{table?.description || '选择数据表查看全部内容、管理记录和查看最近变更。'}</p></div></div>
    <Select className="table-page-select" aria-label="选择数据表" placeholder="选择数据表" value={table?.id} options={tables.filter(item => !item.archived).map(item => ({ value: item.id, label: tableLabel(item) }))} onChange={onSelect} showSearch optionFilterProp="label" />
    {table ? <><Space wrap className="block-gap"><Tag>{total || table.row_count} 条记录</Tag><Tag>{table.fields.length} 个字段</Tag><Tag>版本 {table.version}</Tag><Button icon={<PlusOutlined />} onClick={() => openEditor()}>新增记录</Button><Button onClick={() => onEdit(table)}>编辑表信息</Button><Button onClick={onRelations}>管理关联</Button><Button onClick={onExport}>导出数据</Button><Button danger onClick={() => onDelete(table)}>永久删除此表</Button><Button icon={<ReloadOutlined />} onClick={() => setReload(current => current + 1)}>刷新数据</Button></Space>
      {error && <Alert className="block-gap" type="error" message="读取或操作未完成" description={error} showIcon />}
      <Table<DataRow> className="block-gap" loading={loading} dataSource={rows} rowKey={row => row.id!} scroll={{ x: 'max-content' }} locale={{ emptyText: error ? '读取失败，请重试。' : '当前表暂无记录。' }} pagination={{ current: page, pageSize: 20, total, showSizeChanger: false, showTotal: count => `共 ${count} 条记录`, onChange: next => setPagination({ tableId: table.id, page: next }) }} columns={[
        ...table.fields.map(field => ({ title: field.name, key: field.id, render: (_: unknown, row: DataRow) => <span>{cellText(row.values[field.id])}</span> })),
        { title: '记录操作', key: '_actions', fixed: 'right', render: (_: unknown, row: DataRow) => <Space><Button size="small" disabled={!row.id || busy} onClick={() => openEditor(row)} aria-label={`编辑记录 ${row.id}`}>编辑</Button><Button size="small" danger disabled={!row.id} loading={busy} onClick={() => void preview('delete', row)} aria-label={`删除记录 ${row.id}`}>删除</Button></Space> },
      ]} />
      <Card className="block-gap" title="最近变更记录">{historyError ? <Alert type="error" message="变更记录读取失败" description={historyError} /> : changes.length ? changes.map(change => <div className="operation-line" key={change.id}><Typography.Text>{change.summary || change.action || change.operation || '表数据变更'}</Typography.Text>{change.created_at && <small>{new Date(change.created_at).toLocaleString()}</small>}</div>) : <Empty description="暂无变更记录" />}</Card>
    </> : <Empty className="block-gap" description={tables.length ? '请选择要查看的数据表。' : '暂无数据表，请先在首页新增或导入。'} />}
    <Drawer title={editing?.row ? '编辑记录' : '新增记录'} open={Boolean(editing)} onClose={() => { if (!busy) setEditing(undefined); }} width={560}>
      <Alert type="info" showIcon message="保存前先预览，再确认" description="取消编辑不会修改数据。提交后请在变更预览中核对受影响的记录。" />
      {editorError && <Alert className="block-gap" type="error" message="预览未完成" description={editorError} />}
      {table?.fields.map(field => <label className="form-label" key={field.id}>{field.name}{field.unit && `（${field.unit}）`}
        {field.type === 'boolean' ? <Select aria-label={`记录字段 ${field.name}`} value={values[field.id] === null ? 'null' : String(values[field.id])} onChange={value => setValues(current => ({ ...current, [field.id]: value === 'null' ? null : value === 'true' }))} options={[{ value: 'null', label: '空值' }, { value: 'true', label: '是' }, { value: 'false', label: '否' }]} /> : <Input aria-label={`记录字段 ${field.name}`} type={field.type === 'date' ? 'date' : 'text'} inputMode={field.type === 'number' || field.type === 'integer' ? 'decimal' : undefined} value={String(values[field.id] ?? '')} onChange={event => setValues(current => ({ ...current, [field.id]: event.target.value }))} />}
      </label>)}
      <Space><Button onClick={() => setEditing(undefined)} disabled={busy}>取消编辑</Button><Button type="primary" loading={busy} onClick={() => void preview(editing?.row ? 'update' : 'insert', editing?.row)}>预览记录变更</Button></Space>
    </Drawer>
  </section>;
}
