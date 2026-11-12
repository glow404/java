/** 表和导入共用的字段元数据编辑器，明确区分原文、用户输入与确认推断。 */
import { Input, Select, Table } from 'antd';
import type { Field, Provenance } from './types';

export const provenanceLabel = (value: Field['provenance']) => {
  const source = typeof value === 'string' ? value : value && typeof value === 'object' ? String(value.kind || value.type || 'file') : 'file';
  return ({ file: '文件原文', user: '用户输入', confirmed_inference: '用户确认的推断' } as Record<string, string>)[source] || source;
};
export const fieldTypes = [
  { value: 'string', label: '文本（保留前导零）' }, { value: 'number', label: '数值' },
  { value: 'integer', label: '整数' }, { value: 'boolean', label: '布尔' }, { value: 'date', label: '日期' },
];
export default function FieldEditor({ fields, onChange }: { fields: Field[]; onChange: (fields: Field[]) => void }) {
  const update = (id: string, patch: Partial<Field>) => onChange(fields.map(field => field.id === id ? { ...field, ...patch } : field));
  return <Table size="small" rowKey="id" dataSource={fields} pagination={false} scroll={{ x: 1040 }} columns={[
    { title: '字段 / 标识', width: 185, render: (_, field: Field) => <><Input aria-label={`字段名称 ${field.id}`} value={field.name} onChange={e => update(field.id, { name: e.target.value, provenance: 'user' })} /><small className="field-id">{field.id}</small></> },
    { title: '类型', width: 180, render: (_, field: Field) => <><Select aria-label={`字段类型 ${field.name}`} value={field.type} options={fieldTypes} style={{ width: '100%' }} onChange={value => update(field.id, { type: value, type_provenance: 'user' })} /><small className="field-id">{field.type_provenance === 'inference' ? '解析推断，待用户核对' : field.type_provenance === 'confirmed_inference' ? '用户确认的类型推断' : field.type_provenance === 'user' ? '用户指定类型' : '文件类型'}</small></> },
    { title: '单位', width: 95, render: (_, field: Field) => <Input aria-label={`字段单位 ${field.name}`} value={field.unit || ''} onChange={e => update(field.id, { unit: e.target.value, provenance: 'user' })} /> },
    { title: '自然语言别名', width: 210, render: (_, field: Field) => <Select mode="tags" aria-label={`字段别名 ${field.name}`} value={field.aliases || []} tokenSeparators={[',', '，']} placeholder="例如：分数、得分" style={{ width: '100%' }} onChange={aliases => update(field.id, { aliases, provenance: 'user' })} /> },
    { title: '说明', width: 225, render: (_, field: Field) => <Input.TextArea autoSize={{ minRows: 1, maxRows: 3 }} aria-label={`字段说明 ${field.name}`} value={field.description || ''} onChange={e => update(field.id, { description: e.target.value, provenance: 'user' })} /> },
    { title: '内容来源', width: 180, render: (_, field: Field) => <Select aria-label={`字段来源 ${field.name}`} value={typeof field.provenance === 'string' ? field.provenance : 'file'} options={[{ value: 'file', label: '文件原文' }, { value: 'user', label: '用户输入' }, { value: 'confirmed_inference', label: '用户确认的推断' }]} style={{ width: '100%' }} onChange={(value: Provenance) => update(field.id, { provenance: value })} /> },
  ]} />;
}
