/** 数据首页：汇总当前账号的活动数据表，并展示已登记关联和管理入口。 */
import { Button, Card, Empty, Space, Spin, Statistic, Tag, Typography } from 'antd';
import { DatabaseOutlined, ImportOutlined, PlusOutlined } from '@ant-design/icons';
import type { DataTable, TableRelation } from './types';

interface Props {
  tables: DataTable[]; relations: TableRelation[]; loading: boolean;
  tableLabel: (table: DataTable) => string;
  onOpen: (tableId: string) => void; onCreate: () => void; onImport: () => void;
  onEdit: (table: DataTable) => void; onDelete: (table: DataTable) => void; onRelations: () => void;
}

export default function HomePage({ tables, relations, loading, tableLabel, onOpen, onCreate, onImport, onEdit, onDelete, onRelations }: Props) {
  const activeTables = tables.filter(table => !table.archived);
  const relationLabel = (tableId: string, fieldId: string) => {
    const table = tables.find(item => item.id === tableId);
    return `${table ? tableLabel(table) : tableId} · ${table?.fields.find(field => field.id === fieldId)?.name || fieldId}`;
  };
  return <section className="home-page" aria-label="数据首页">
    <div className="workbench-heading"><div><h1>首页</h1><p>浏览数据表、管理数据，以及核对表之间的关联规则。</p></div><Space wrap><Button icon={<ImportOutlined />} onClick={onImport}>导入数据表</Button><Button type="primary" icon={<PlusOutlined />} onClick={onCreate}>新增数据表</Button></Space></div>
    <Card className="home-summary"><Statistic title="当前账号数据库中的数据表" value={activeTables.length} suffix="张" prefix={<DatabaseOutlined />} /></Card>
    <Spin spinning={loading}><div className="home-table-grid block-gap">
      {activeTables.length ? activeTables.map(table => <Card key={table.id} className="home-table-card" title={<h2>{tableLabel(table)}</h2>}>
        <Typography.Paragraph className="table-description">{table.description || '暂无表描述，可通过编辑表信息补充。'}</Typography.Paragraph>
        <Space wrap><Tag>{table.row_count} 条记录</Tag><Tag>{table.fields.length} 个字段</Tag><Tag>版本 {table.version}</Tag></Space>
        <Space wrap className="block-gap"><Button onClick={() => onOpen(table.id)} aria-label={`查看 ${tableLabel(table)}`}>查看内容</Button><Button onClick={() => onEdit(table)} aria-label={`编辑 ${tableLabel(table)}`}>编辑表信息</Button><Button danger onClick={() => onDelete(table)} aria-label={`永久删除 ${tableLabel(table)}`}>永久删除</Button></Space>
      </Card>) : <Card><Empty description="暂无数据表，导入文件或新增空表即可开始。" /></Card>}
    </div></Spin>
    <Card className="block-gap" title="数据表关联规则" extra={<Button onClick={onRelations}>管理关联</Button>}>
      <Typography.Paragraph type="secondary">这里展示已登记的规则；新增或变更关联需要预览并确认。</Typography.Paragraph>
      {relations.length ? relations.map((relation, index) => <div className="relation-row" key={relation.id || index}>
        <Typography.Text>{relationLabel(relation.left_table_id, relation.left_field_id)} ↔ {relationLabel(relation.right_table_id, relation.right_field_id)}</Typography.Text>
        <Space wrap><Tag>{relation.join_type === 'left' ? '左连接：保留左表记录' : '内连接：仅保留匹配记录'}</Tag><Tag color={relation.status === 'inactive' ? undefined : 'green'}>{relation.status === 'inactive' ? '已停用' : '已确认'}</Tag>{relation.stale && <Tag color="orange">版本变化，需重新确认</Tag>}{relation.archived && <Tag>关联表已归档</Tag>}</Space>
        {relation.reason && <Typography.Paragraph type="secondary">{relation.reason}</Typography.Paragraph>}
      </div>) : <Empty description="暂无已登记关联，可在管理关联中核对候选规则。" />}
    </Card>
  </section>;
}
