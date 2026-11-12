/** 展示助手为操作计划提出的建议口径、依据和替代项，帮助用户确认或追问修改。 */
import { Alert } from 'antd';
import type { ChangePlan, PlanAssumption } from './types';

export function readableAssumptions(value: unknown): PlanAssumption[] {
  if (!Array.isArray(value)) return [];
  // 兼容旧快照和手工结构化计划；异常字段不参与渲染，不能把任意 JSON 当作可信事实。
  return value.filter((item): item is PlanAssumption => item !== null && typeof item === 'object'
    && typeof item.subject === 'string' && typeof item.choice === 'string' && typeof item.reason === 'string'
    && Array.isArray(item.alternatives) && item.alternatives.every((alternative: unknown) => typeof alternative === 'string'));
}

export function planAssumptions(plan?: ChangePlan): PlanAssumption[] {
  const nested = (value: unknown): unknown => value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>).assumptions : undefined;
  // 服务器保存的结构化计划是依据，不能在浏览器重新推断单位或指标含义。
  for (const source of [plan?.assumptions, nested(plan?.query_snapshot), nested(plan?.plan)]) {
    const assumptions = readableAssumptions(source);
    if (assumptions.length) return assumptions;
  }
  return [];
}

export default function AssumptionPreview({ assumptions, pending }: { assumptions: unknown; pending?: boolean }) {
  const items = readableAssumptions(assumptions);
  if (!items.length) return null;
  const guidance = pending
    ? '这些判断是建议口径，并非已确认事实。核对后可确认执行；需调整时点击“追问修改此计划”。'
    : '以下是助手为本计划提出的判断及依据，执行情况以计划状态为准。可打开操作预览核对，或向助手说明要修改的口径。';
  return <Alert className="block-gap" showIcon type={pending ? 'warning' : 'info'}
    message={pending ? '建议口径（待确认）' : '本计划的建议口径'}
    description={<><p>{guidance}</p><ol className="assumption-list">{items.map((item, index) => <li key={index}>
      <strong>{item.subject}：</strong><span>{item.choice}</span>
      <div>判断依据：{item.reason}</div>
      {item.alternatives.length > 0 && <div>可修改为：{item.alternatives.join('；')}</div>}
    </li>)}</ol></>} />;
}
