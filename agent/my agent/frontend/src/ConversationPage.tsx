/** 对话结果页面：展示当前对话保存的快照，切换页面不重新执行查询。 */
import { Button, Card, Collapse, Empty, Space, Typography } from 'antd';
import { DeleteOutlined, FileTextOutlined } from '@ant-design/icons';
import ResultView from './ResultView';
import type { ConversationDetail, DataTable, QueryResult } from './types';

interface Props {
  conversation?: ConversationDetail; tables: DataTable[]; hiddenResultIds: string[];
  tableLabel: (table?: DataTable) => string;
  onVisibility: (id: string, hide: boolean) => void; onView: (id: string) => void;
}

export default function ConversationPage({ conversation, tables, hiddenResultIds, tableLabel, onVisibility, onView }: Props) {
  const saved = conversation?.results || [];
  const visible = saved.filter(result => !hiddenResultIds.includes(result.id));
  const hidden = saved.filter(result => hiddenResultIds.includes(result.id));
  const title = conversation?.last_question || conversation?.messages.filter(item => item.role === 'user').at(-1)?.content || '新对话';
  const renderResult = (result: QueryResult) => <Card key={result.id} id={`result-${result.id}`} data-result-card-id={result.id} className="block-gap result-card"
    title={<Space><FileTextOutlined />{tableLabel(tables.find(table => table.id === result.table_id))}</Space>}
    extra={<Space wrap><Typography.Text type="secondary">{result.created_at ? new Date(result.created_at).toLocaleString('zh-CN') : ''}</Typography.Text>
      <Button size="small" type="text" icon={<DeleteOutlined />} aria-label={`隐藏结果 ${result.id}`} onClick={() => onVisibility(result.id, true)} /></Space>}>
    <ResultView result={result} />
  </Card>;
  return <section className="conversation-page">
    <div className="workbench-heading"><div><div className="section-eyebrow">RECENT CONVERSATION</div><h1>对话与结果</h1><p>{title}</p></div></div>
    <Typography.Paragraph type="secondary">右侧继续提问。下方结果来自服务器保存的快照，每次操作保留各自的查询范围。</Typography.Paragraph>
    {visible.length ? visible.map(renderResult) : <Empty description="当前对话还没有可展示的结果" />}
    {hidden.length > 0 && <Collapse className="block-gap" items={[{ key: 'hidden', label: `已隐藏结果 (${hidden.length}) · 可恢复显示`, children: hidden.map(result =>
      <div key={result.id} className="operation-line"><span className="snapshot-id">{result.id}</span><Space wrap>
        <Button size="small" onClick={() => onView(result.id)}>查看保留快照</Button>
        <Button size="small" aria-label={`恢复显示 ${result.id}`} onClick={() => onVisibility(result.id, false)}>恢复此结果显示</Button>
      </Space></div>) }]} />}
  </section>;
}
