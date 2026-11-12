/** 导入核对工作流：等待后台解析、查看原结构、修正候选结构，再显式确认入库。 */
import { useEffect, useState } from 'react';
import { Alert, App as AntApp, Button, Checkbox, Collapse, Drawer, Input, Pagination, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { api, errorText, jsonBody } from './api';
import FieldEditor from './FieldEditor';
import { printable } from './ResultView';
import type { Correction, DataRow, Field, ImportCandidate, ImportPreview, Task } from './types';

function correctedValue(text: string, field: Field): unknown {
  if (text === '') return null;
  if (['number', 'integer'].includes(field.type) && Number.isFinite(Number(text))) return Number(text);
  if (field.type === 'boolean' && ['true', 'false'].includes(text)) return text === 'true';
  return text;
}
function candidateCell(candidate: ImportCandidate, rowIndex: number, fieldId: string) {
  const corrected = candidate.corrections?.find(c => c.row_index === rowIndex && c.field_id === fieldId);
  return corrected ? corrected.value : candidate.rows[rowIndex - (candidate.row_offset || 0)].values[fieldId];
}

function OriginalStructure({ candidate }: { candidate: ImportCandidate }) {
  const original = candidate.original && typeof candidate.original === 'object' ? candidate.original as Record<string, unknown> : {};
  const sourceRows = Array.isArray(original.rows) ? original.rows : [];
  const normalized = sourceRows.map((row, i) => Array.isArray(row) ? { row_number: i + 1, values: row } : row as { row_number: number; values: unknown[] });
  const width = Math.max(0, ...normalized.map(row => Array.isArray(row.values) ? row.values.length : 0));
  return <>
    <div className="block-gap"><Space wrap><Tag>原结构 {String(original.source_row_count ?? sourceRows.length)} 行</Tag><Tag color="cyan">候选 {candidate.row_count} 行</Tag><Typography.Text type="secondary">变化 {String(original.row_count_change ?? candidate.row_count - sourceRows.length)} 行（标题 / 表头或汇总拆分导致）</Typography.Text></Space></div>
    {normalized.length > 0 && <Collapse className="block-gap" items={[{ key: 'source-rows', label: '对照文件原表（保留标题、表头与空值）', children: <Table size="small" dataSource={normalized} rowKey={row => String(row.row_number)} pagination={{ pageSize: 5, showSizeChanger: false }} scroll={{ x: 'max-content' }} columns={[{ title: '原始行号', dataIndex: 'row_number', width: 90 }, ...Array.from({ length: width }, (_, index) => ({ title: `原列 ${index + 1}`, key: String(index), width: 140, render: (_: unknown, row: { values: unknown[] }) => printable(row.values[index]) }))]} /> }]} />}
    <Collapse className="block-gap" items={[{ key: 'source-details', label: '详细解析依据（工作表 / 页码、表头及合并范围）', children: <pre className="json-preview">{JSON.stringify(candidate.original, null, 2)}</pre> }]} />
  </>;
}

export default function ImportDrawer({ open, taskId, onClose, onImported }: { open: boolean; taskId?: string; onClose: () => void; onImported: (tableIds?: string[]) => void }) {
  const { message } = AntApp.useApp();
  const [task, setTask] = useState<Task>();
  const [preview, setPreview] = useState<ImportPreview>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [paging, setPaging] = useState(false);
  useEffect(() => {
    if (!open || !taskId) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    setPreview(undefined); setError(''); setTask(undefined);
    const poll = async () => {
      try {
        const nextTask = await api<Task>(`/tasks/${taskId}`);
        if (stopped) return;
        setTask(nextTask);
        if (nextTask.status === 'completed') {
          const nextPreview = await api<ImportPreview>(`/imports/${taskId}`);
          if (!stopped) {
            const selectedGroups = new Set<string>();
            setPreview({ ...nextPreview, tables: nextPreview.tables.map(t => {
              const group = t.mutually_exclusive_group || t.selection_group;
              const selected = !group || !selectedGroups.has(group);
              if (group) selectedGroups.add(group);
              return { ...t, selected, reviewed: false, corrections: [] };
            }) });
          }
        } else if (nextTask.status !== 'failed') timer = setTimeout(() => { void poll(); }, 2000);
      } catch (failure) { if (!stopped) setError(errorText(failure)); }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [open, taskId]);

  const update = (candidateId: string, patch: Partial<ImportCandidate>) => setPreview(current => current ? ({ ...current, tables: current.tables.map(t => t.candidate_id === candidateId ? { ...t, ...patch } : t) }) : current);
  const loadCandidatePage = async (candidate: ImportCandidate, page: number) => {
    if (!taskId) return;
    setPaging(true); setError('');
    try {
      const next = await api<ImportPreview>(`/imports/${taskId}?candidate_id=${encodeURIComponent(candidate.candidate_id)}&offset=${(page - 1) * 200}&limit=200`);
      const incoming = next.tables[0];
      // 只更新当前预览窗口，保留已经编辑的字段、修正及核对状态。
      update(candidate.candidate_id, { rows: incoming.rows, original: incoming.original, row_offset: incoming.row_offset });
    } catch (failure) { setError(`${errorText(failure)}；已有修正已保留。`); }
    finally { setPaging(false); }
  };
  const selectCandidate = (candidate: ImportCandidate, selected: boolean) => setPreview(current => current ? ({ ...current, tables: current.tables.map(t => {
    const group = candidate.mutually_exclusive_group || candidate.selection_group;
    const sameGroup = group && group === (t.mutually_exclusive_group || t.selection_group);
    if (t.candidate_id === candidate.candidate_id) return { ...t, selected };
    return selected && sameGroup ? { ...t, selected: false } : t;
  }) }) : current);
  const retry = async () => {
    if (!taskId) return;
    setBusy(true); setError('');
    try { await api(`/tasks/${taskId}/retry`, { method: 'POST' }); onClose(); message.info('重试已加入队列，请从后台任务重新打开核对。'); onImported(); }
    catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); }
  };
  const confirm = async () => {
    if (!preview) return;
    setBusy(true); setError('');
    try {
      const selected = preview.tables.filter(t => t.selected);
      if (!selected.length || selected.some(t => !t.reviewed)) throw new Error('请至少选择一张候选表，并逐张勾选“已核对结构与归属”。');
      const response = await api<{ table_ids: string[] }>(`/imports/${preview.task_id}/confirm`, { method: 'POST', body: jsonBody({ version: preview.version, tables: selected.map(t => ({ candidate_id: t.candidate_id, name: t.name, description: t.description, fields: t.fields, corrections: t.corrections || [], reviewed: true })) }) });
      message.success('核对后的数据已保存到当前账号。'); onImported(response.table_ids); onClose();
    } catch (failure) { setError(`${errorText(failure)}；当前核对内容已保留，可修正后重试。`); }
    finally { setBusy(false); }
  };

  return <Drawer title="导入核对" open={open} onClose={onClose} width={1040} destroyOnHidden={false}
    extra={<Button type="primary" loading={busy} disabled={!preview} onClick={() => void confirm()}>确认选中表入库</Button>}>
    <Alert type="info" showIcon message="先核对，再保存" description="合并范围外的空值保持为空。没有明确合并依据的归属、不同汇总粒度和复杂表结构须逐项核对；模型推断不能标为文件原文。" />
    {error && <Alert className="block-gap" type="error" showIcon message="未保存" description={error} />}
    {task && <div className="block-gap"><Tag color={task.status === 'failed' ? 'red' : task.status === 'completed' ? 'green' : 'blue'}>{({ pending: '等待解析', running: '正在解析', completed: '解析完成', failed: '解析失败' })[task.status]}</Tag><Typography.Text type="secondary">任务 {task.id} · 尝试 {task.attempts || 1} 次</Typography.Text></div>}
    {task?.status === 'failed' && <Alert className="block-gap" type="error" showIcon message={task.error || '解析失败'} action={<Button loading={busy} onClick={() => void retry()}>重试解析</Button>} />}
    {!preview && task?.status !== 'failed' && !error && <div className="loading-panel"><Spin /><p>后台保存并解析文件，关闭此面板后可从任务列表恢复。</p></div>}
    {preview && <>
      {preview.warnings?.length > 0 && <Alert className="block-gap" type="warning" showIcon message="文件级核对事项" description={<ul>{preview.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>} />}
      <Tabs className="block-gap" items={preview.tables.map(candidate => ({ key: candidate.candidate_id, label: <span>{candidate.name} <Tag>{candidate.row_count} 行</Tag></span>, children: <>
        <Space className="candidate-controls" wrap><Checkbox checked={candidate.selected} onChange={e => selectCandidate(candidate, e.target.checked)}>选择此表导入</Checkbox><Tag>候选行数 {candidate.row_count}</Tag><Tag>预览 {candidate.rows.length} 行</Tag></Space>
        {(candidate.mutually_exclusive_group || candidate.selection_group) && <Alert type="warning" showIcon message="这是同一原表的备选结构，只能选择一种" description="选中此候选会自动取消同源的其他结构。请对照原表，确定首行属于表头还是数据。" />}
        <label className="form-label">表名<Input value={candidate.name} aria-label="候选表名" onChange={e => update(candidate.candidate_id, { name: e.target.value })} /></label>
        <label className="form-label">表说明<Input.TextArea value={candidate.description} aria-label="候选表说明" onChange={e => update(candidate.candidate_id, { description: e.target.value })} /></label>
        {candidate.warnings?.length > 0 && <Alert className="block-gap" type="warning" showIcon message="该表需要核对" description={<ul>{candidate.warnings.map((warning, i) => <li key={i}>{warning}</li>)}</ul>} />}
        <OriginalStructure candidate={candidate} />
        <h3>候选字段与映射</h3><Typography.Paragraph type="secondary">字段 ID 保持不变；可修正映射名称、类型、单位和说明。学号及订单号建议使用文本以保留前导零。</Typography.Paragraph>
        <FieldEditor fields={candidate.fields} onChange={fields => update(candidate.candidate_id, { fields, reviewed: false })} />
        <h3>候选记录与归属修正</h3><Typography.Paragraph type="secondary">只在有依据时修正单元格。清空表示空值；布尔值使用 true / false。源行、工作表 / 页码和合并依据保留在来源列。预览以服务端返回范围为准。</Typography.Paragraph>
        {candidate.row_count > 200 && <Pagination className="block-gap" aria-label="候选预览窗口" current={Math.floor((candidate.row_offset || 0) / 200) + 1} pageSize={200} total={candidate.row_count} showSizeChanger={false} onChange={page => void loadCandidatePage(candidate, page)} />}
        <Table<DataRow & { row_index: number }> size="small" loading={paging} rowKey="row_index" dataSource={candidate.rows.map((row, row_index) => ({ ...row, row_index: row_index + (candidate.row_offset || 0) }))} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 'max-content' }} columns={[
          { title: '候选行', dataIndex: 'row_index', width: 80, render: (index: number) => index + 1 },
          ...candidate.fields.map(field => ({ title: field.name, width: 170, render: (_: unknown, row: { row_index: number }) => {
            const value = candidateCell(candidate, row.row_index, field.id);
            return <Input aria-label={`候选行 ${row.row_index + 1} ${field.name}`} value={value === null || value === undefined ? '' : String(value)} onChange={event => {
              const correction: Correction = { row_index: row.row_index, field_id: field.id, value: correctedValue(event.target.value, field) };
              update(candidate.candidate_id, { corrections: [...(candidate.corrections || []).filter(c => !(c.row_index === correction.row_index && c.field_id === field.id)), correction], reviewed: false });
            }} />;
          } })),
          { title: '来源与公式 / 合并依据', width: 300, render: (_: unknown, row: { source?: unknown }) => <pre className="source-cell">{JSON.stringify(row.source, null, 2)}</pre> },
        ]} />
        <Checkbox className="review-checkbox" checked={candidate.reviewed} onChange={e => update(candidate.candidate_id, { reviewed: e.target.checked })}>我已核对该表的字段、行数、汇总粒度和记录归属，确认以上修正</Checkbox>
      </> }))} />
      {preview.tables.length === 0 && <Alert type="warning" message="没有可导入的表格。请检查文件内容，扫描件或复杂跨页表需要人工整理。" />}
    </>}
  </Drawer>;
}
