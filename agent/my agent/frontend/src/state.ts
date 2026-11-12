/** 保留服务器结果快照与侧栏偏好；禁止助手和工作台自行重复计算查询。 */
import type { QueryResult } from './types';

export type ResultRegistry = Record<string, QueryResult>;
export function retainResults(existing: ResultRegistry, incoming: QueryResult[]): ResultRegistry {
  const next = { ...existing };
  for (const result of incoming) next[result.id] = result;
  return next;
}
export const WIDTHS = { left: { default: 244, min: 190, max: 430 }, right: { default: 390, min: 300, max: 640 } };
export type Side = keyof typeof WIDTHS;
export function clampWidth(side: Side, width: number) {
  return Math.max(WIDTHS[side].min, Math.min(WIDTHS[side].max, Math.round(width)));
}
export function loadWidth(side: Side, accountId: string) {
  const saved = Number(localStorage.getItem(`vibe:${accountId}:width:${side}`));
  return saved > 0 && Number.isFinite(saved) ? clampWidth(side, saved) : WIDTHS[side].default;
}
export function saveWidth(side: Side, accountId: string, width: number) {
  localStorage.setItem(`vibe:${accountId}:width:${side}`, String(clampWidth(side, width)));
}
