/** 展示同一个服务器结果快照：表格、统计口径和由实际返回数据生成的图表。 */
import { useEffect, useRef, useState } from 'react';
import { Alert, Collapse, Table, Tag, Typography } from 'antd';
import type { ChartSpec, QueryResult } from './types';

export function printable(value: unknown): string {
  if (value === null || value === undefined) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

export function safeChart(chart?: ChartSpec) {
  if (!chart || chart.type === 'none') return { chart, notice: '' };
  // 各组占比的分母互相独立，饼图无法表达“整体组成”，因此保留真实值改为柱图。
  if (chart.type === 'pie' && (chart.unit === '%' || chart.numerator || chart.denominator)) {
    return { chart: { ...chart, type: 'bar' as const }, notice: '每组占比各有分母，已改用柱状图，避免将独立比率误表示为整体组成。' };
  }
  return { chart, notice: '' };
}

function QueryChart({ result, spec }: { result: QueryResult; spec: ChartSpec }) {
  const container = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  const valid = Boolean(spec.x && spec.y && result.columns.some(c => c.id === spec.x) && result.columns.some(c => c.id === spec.y));
  useEffect(() => {
    if (!container.current || !valid) return;
    let disposed = false;
    let cleanup = () => {};
    // 动态加载 ECharts 降低登录页负担；图表只读取已经保存的查询快照，不再次查询。
    setError('');
    void import('./chartEngine').then(echarts => {
      if (disposed || !container.current) return;
      const chart = echarts.init(container.current);
      const x = spec.x!; const y = spec.y!;
      const rows = result.rows.filter(row => typeof row[y] === 'number' && Number.isFinite(row[y]));
      const axisName = result.columns.find(c => c.id === y)?.name;
      chart.setOption({
        color: ['#167e80', '#586cc3', '#d1a052', '#c86675', '#58a79a'],
        tooltip: { trigger: spec.type === 'pie' ? 'item' : 'axis', confine: true, renderMode: 'richText' },
        grid: { left: 55, right: 20, top: 28, bottom: 60 },
        ...(spec.type === 'pie' ? {} : {
          xAxis: { type: 'category', name: result.columns.find(c => c.id === x)?.name, nameLocation: 'middle', nameGap: 45, data: rows.map(row => printable(row[x])), axisLabel: { interval: 0, rotate: rows.length > 5 ? 25 : 0, overflow: 'truncate', width: 90 } },
          yAxis: { type: 'value', name: spec.unit || axisName, axisLabel: { formatter: `{value}${spec.unit || ''}` } },
        }),
        series: [spec.type === 'pie'
          ? { type: 'pie', radius: ['35%', '68%'], data: rows.map(row => ({ name: printable(row[x]), value: row[y] })), label: { formatter: '{b}: {c}' } }
          : { type: spec.type, data: rows.map(row => row[y]), barMaxWidth: 45, smooth: false, itemStyle: { borderRadius: [4, 4, 0, 0] } }],
      });
      const observer = new ResizeObserver(() => chart.resize());
      observer.observe(container.current);
      cleanup = () => { observer.disconnect(); chart.dispose(); };
    }).catch(failure => { if (!disposed) setError(`图表加载失败：${failure instanceof Error ? failure.message : String(failure)}。表格与结果快照仍可查看。`); });
    return () => { disposed = true; cleanup(); };
  }, [result, spec, valid]);
  if (!valid) return <Alert type="warning" showIcon message="图表字段与结果列不匹配，已保留表格结果，请核对查询口径。" />;
  return <>{error && <Alert type="error" showIcon message={error} />}<div className="query-chart" ref={container} role="img" aria-label={`${spec.title || '查询结果图'}，数据来自结果 ${result.id}`} /></>;
}

export default function ResultView({ result, compact = false }: { result: QueryResult; compact?: boolean }) {
  const { chart, notice } = safeChart(result.chart);
  return <section className={`result-view ${compact ? 'compact-result' : ''}`} data-result-id={result.id}>
    <div className="result-ident"><Tag color="cyan">已保存快照</Tag><Typography.Text type="secondary" className="snapshot-id" title={result.id}>结果 {result.id}</Typography.Text></div>
    <p className="result-explanation">{result.explanation}</p>
    <Table size="small" rowKey="__snapshot_key" dataSource={result.rows.map((row, index) => ({ ...row, __snapshot_key: `${result.id}:${index}` }))}
      columns={result.columns.map(c => ({ title: `${c.name}${c.unit ? `（${c.unit}）` : ''}`, dataIndex: c.id, key: c.id, render: printable, ellipsis: false }))}
      pagination={result.rows.length > (compact ? 5 : 10) ? { pageSize: compact ? 5 : 10, size: 'small', showSizeChanger: false } : false}
      scroll={{ x: 'max-content' }} locale={{ emptyText: '当前结果页没有记录，请查看上方原因说明' }} />
    {notice && <Alert className="chart-notice" type="info" showIcon message={notice} />}
    {chart && chart.type !== 'none' && <>
      <h4 className="chart-title">{chart.title || '本次查询图表'}</h4>
      <QueryChart result={result} spec={chart} />
      <div className="chart-definition">
        <span>统计口径：{result.explanation}</span>
        <span>单位：{chart.unit || result.columns.find(c => c.id === chart.y)?.unit || '与结果列一致'}</span>
        {chart.numerator && <span>分子：{chart.numerator}</span>}
        {chart.denominator && <span>分母：{chart.denominator}</span>}
      </div>
    </>}
    {chart?.reason && <Typography.Paragraph type="secondary">图型说明：{chart.reason}</Typography.Paragraph>}
    <Collapse ghost size="small" items={[{ key: 'snapshot', label: '查询依据与快照', children: <pre className="json-preview">{JSON.stringify(result.query_snapshot, null, 2)}</pre> }]} />
  </section>;
}
