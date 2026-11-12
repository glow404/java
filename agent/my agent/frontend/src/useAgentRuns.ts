/** 持久异步提问：恢复服务器任务、轮询状态并向服务器发送中止请求。 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorText, jsonBody } from './api';
import type { AgentRun, ChatResponse } from './types';

const pending = (run?: AgentRun) => Boolean(run && ['queued', 'running'].includes(run.status));
export default function useAgentRuns(conversationId: string | undefined, onResponse: (response: ChatResponse, id: string) => Promise<void>, onSettled: (id: string) => Promise<void>) {
  const [run, setRun] = useState<AgentRun>();
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState('');
  const callbacks = useRef({ onResponse, onSettled });
  const currentConversation = useRef(conversationId);
  currentConversation.current = conversationId;
  callbacks.current = { onResponse, onSettled };
  useEffect(() => {
    setRun(undefined); setError('');
    if (!conversationId) return;
    let stopped = false;
    void api<{ runs: AgentRun[] } | AgentRun[]>(`/conversations/${conversationId}/runs`).then(value => {
      const runs = Array.isArray(value) ? value : value.runs;
      if (!stopped) setRun(current => current?.conversation_id === conversationId ? current : runs.find(item => pending(item)));
    }).catch(failure => { if (!stopped) setError(`提问任务恢复失败：${errorText(failure)}`); });
    return () => { stopped = true; };
  }, [conversationId]);
  useEffect(() => {
    if (!run || !pending(run)) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await api<AgentRun>(`/runs/${run.id}`);
        if (stopped) return;
        setRun(next);
        // 请求已落库不代表队列已接收；保留轮询的同时显示可恢复错误，恢复后清除旧提示。
        setError(next.error || (next.status === 'failed' ? '提问未完成，服务器没有执行未确认的操作。' : ''));
        if (next.status === 'completed' && next.response) await callbacks.current.onResponse(next.response, next.conversation_id);
        else if (!pending(next)) {
          await callbacks.current.onSettled(next.conversation_id);
        } else timer = setTimeout(() => { void poll(); }, 1500);
      } catch (failure) {
        if (!stopped) { setError(`提问状态读取失败：${errorText(failure)}。任务仍保存在服务器，可重试或中止。`); timer = setTimeout(() => { void poll(); }, 3000); }
      }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [run?.id, run?.status]);
  const start = useCallback(async (content: string, tableIds: string[], structuredPlan?: unknown) => {
    if (!conversationId || pending(run) || starting) return false;
    setStarting(true); setError('');
    try {
      const created = await api<AgentRun>(`/conversations/${conversationId}/runs`, { method: 'POST', body: jsonBody({ content, table_ids: tableIds, ...(structuredPlan ? { plan: structuredPlan } : {}) }) });
      // 用户可能在提交请求期间切换对话；旧任务留在服务器，不能覆盖新对话的任务状态。
      if (currentConversation.current === conversationId) { setRun(created); await callbacks.current.onSettled(conversationId); }
      return true;
    } catch (failure) { setError(errorText(failure)); return false; }
    finally { setStarting(false); }
  }, [conversationId, run, starting]);
  const cancel = async () => {
    if (!run || !pending(run)) return;
    setCancelling(true); setError('');
    try {
      // 中止必须修改服务器任务；仅 abort 浏览器请求不能阻止后台模型继续生成计划。
      const next = await api<AgentRun>(`/runs/${run.id}/cancel`, { method: 'POST' });
      if (currentConversation.current !== next.conversation_id) return;
      setRun(next);
      if (next.status === 'completed' && next.response) await callbacks.current.onResponse(next.response, next.conversation_id);
      else await callbacks.current.onSettled(next.conversation_id);
    } catch (failure) { setError(`中止失败：${errorText(failure)}`); }
    finally { setCancelling(false); }
  };
  return { run, active: pending(run), starting, cancelling, error, start, cancel };
}
