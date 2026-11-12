/** vibe 数据工作台：登录、账号目录、异步导入、持久对话及确认式数据变更。 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter, Link, Navigate, Route, Routes, matchPath, useLocation, useNavigate } from 'react-router';
import { Alert, App as AntApp, Avatar, Badge, Button, Card, Collapse, Drawer, Empty, Form, Input, Modal, Select, Space, Spin, Table, Tag, Tooltip, Typography } from 'antd';
import { ArrowRightOutlined, DatabaseOutlined, EditOutlined, FileTextOutlined, HistoryOutlined, LogoutOutlined, PlusOutlined, ReloadOutlined, RobotOutlined, SendOutlined, SettingOutlined, StopOutlined, UserOutlined } from '@ant-design/icons';
import { api, ApiError, downloadUrl, errorText, jsonBody, setCsrfToken } from './api';
import FieldEditor from './FieldEditor';
import ImportDrawer from './ImportDrawer';
import PlanModal from './PlanModal';
import ResizableSeparator from './ResizableSeparator';
import TableManager from './TableManager';
import useAgentRuns from './useAgentRuns';
import HomePage from './HomePage';
import TablePage from './TablePage';
import ProfilePage from './ProfilePage';
import ConversationPage from './ConversationPage';
import ResultView from './ResultView';
import { loadWidth, retainResults, saveWidth } from './state';
import type { ResultRegistry, Side } from './state';
import type { Catalog, ChangePlan, ChatResponse, Conversation, ConversationDetail, DataTable, Operation, QueryResult, TableRelation, Task, User } from './types';

const taskLabels: Record<Task['status'], string> = { pending: '等待处理', running: '正在处理', completed: '已完成', failed: '处理失败' };
const taskColors = { pending: 'default', running: 'processing', completed: 'success', failed: 'error' } as const;
const blankCatalog: Catalog = { tables: [], tasks: [], recent_operations: [], model_mode: 'disabled' };

export default function App() {
  return <BrowserRouter><AuthenticatedApp /></BrowserRouter>;
}

function AuthenticatedApp() {
  const [user, setUser] = useState<User>();
  const [checked, setChecked] = useState(false);
  const [loginError, setLoginError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reauthNeeded, setReauthNeeded] = useState(false);
  useEffect(() => {
    const expired = () => setReauthNeeded(true);
    window.addEventListener('vibe:session-expired', expired);
    return () => window.removeEventListener('vibe:session-expired', expired);
  }, []);
  useEffect(() => { void api<User>('/auth/me').then(next => { setCsrfToken(next.csrf_token); setUser(next); }).catch(failure => { if (!(failure instanceof ApiError && failure.status === 401)) setLoginError(errorText(failure)); }).finally(() => setChecked(true)); }, []);
  const login = async (credentials: { username: string; password: string }) => {
    setBusy(true); setLoginError('');
    try {
      await api('/auth/login', { method: 'POST', body: jsonBody(credentials) });
      const next = await api<User>('/auth/me'); setCsrfToken(next.csrf_token); setUser(next); setReauthNeeded(false);
    } catch (failure) { setLoginError(errorText(failure)); }
    finally { setBusy(false); }
  };
  const logout = async () => {
    try { await api('/auth/logout', { method: 'POST' }); }
    catch (failure) { if (!(failure instanceof ApiError && failure.status === 401)) throw failure; }
    setCsrfToken(''); setUser(undefined); setReauthNeeded(false);
  };
  if (!checked) return <div className="app-loading"><Spin size="large" /><p>恢复登录会话…</p></div>;
  // 重新登录通过遮罩恢复会话，保持同账号工作台挂载，以保留保存失败的编辑草稿。
  if (user) return <><Workspace key={user.id} user={user} onLogout={logout} onUserUpdated={next => setUser(current => current ? { ...current, ...next } : next)} onPasswordChanged={() => { setCsrfToken(''); setUser(undefined); setReauthNeeded(false); }} /><Modal title="登录会话已过期" open={reauthNeeded} footer={null} closable={false} maskClosable={false}>
    <Alert type="info" message="请重新登录后重试；当前输入与编辑草稿已保留。旧变更计划需要重新预览。" />
    {loginError && <Alert className="block-gap" type="error" message={loginError} />}
    <Form className="block-gap" layout="vertical" initialValues={{username:user.username}} onFinish={values => void login(values)}><Form.Item name="username" label="账号"><Input disabled /></Form.Item><Form.Item name="password" label="密码" rules={[{required:true,message:'请输入密码'}]}><Input.Password autoComplete="current-password" /></Form.Item><Button htmlType="submit" type="primary" loading={busy}>重新登录</Button></Form>
  </Modal></>;
  return <div className="login-shell">
    <section className="login-story"><div className="brand"><span className="brand-icon"><DatabaseOutlined /></span><span>vibe<span className="brand-sub">数据智能体</span></span></div><div className="login-story-content"><Tag color="cyan">公司内网 · 数据工作空间</Tag><h1>让每一次提问，<br />都有数据依据。</h1><p>导入并核对文件，用中文探索数据。<br />查询留存快照，变更先预览再确认。</p><div className="login-flow"><span>01 核对导入</span><ArrowRightOutlined /><span>02 数据查询</span><ArrowRightOutlined /><span>03 确认变更</span></div></div><small>你的文件、数据与对话按账号保存于服务器。</small></section>
    <section className="login-form-panel"><div className="login-form"><h2>登录工作空间</h2><p className="muted">使用管理员为你创建的公司账号。</p>{loginError && <Alert showIcon type="error" message="登录失败" description={loginError} className="block-gap" />}
      <Form layout="vertical" onFinish={values => void login(values)} requiredMark={false}>
        <Form.Item name="username" label="用户名或邮箱" rules={[{ required: true, message: '请输入用户名或绑定邮箱' }]}><Input size="large" autoComplete="username" prefix={<UserOutlined />} placeholder="用户名或绑定邮箱" /></Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}><Input.Password size="large" autoComplete="current-password" placeholder="输入密码" /></Form.Item>
        <Button type="primary" size="large" htmlType="submit" loading={busy} block>登录</Button>
      </Form><div className="login-note"><SettingOutlined /> 首次使用请由管理员按项目说明初始化账号；系统没有公开默认密码。</div>
    </div></section>
  </div>;
}

function Workspace({ user, onLogout, onUserUpdated, onPasswordChanged }: { user: User; onLogout: () => Promise<void>; onUserUpdated: (user: User) => void; onPasswordChanged: () => void }) {
  const { message } = AntApp.useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const routeTableId = matchPath('/tables/:tableId', location.pathname)?.params.tableId;
  const routeConversationId = matchPath('/conversations/:conversationId', location.pathname)?.params.conversationId;
  const [catalog, setCatalog] = useState<Catalog>(blankCatalog);
  const [loading, setLoading] = useState(true);
  const [globalError, setGlobalError] = useState('');
  const [selectedTableId, setSelectedTableId] = useState<string>();
  const [scopeTableIds, setScopeTableIds] = useState<string[]>([]);
  const [selectedRelation, setSelectedRelation] = useState<TableRelation>();
  const [relations, setRelations] = useState<TableRelation[]>([]);
  const [leftWidth, setLeftWidth] = useState(() => loadWidth('left', user.id));
  const [rightWidth, setRightWidth] = useState(() => loadWidth('right', user.id));
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string>();
  const [conversation, setConversation] = useState<ConversationDetail>();
  const [results, setResults] = useState<ResultRegistry>({});
  const [composer, setComposer] = useState('');
  const [chatError, setChatError] = useState('');
  const [uploadBusy, setUploadBusy] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importTaskId, setImportTaskId] = useState<string>();
  const [metadata, setMetadata] = useState<DataTable>();
  const [metadataBusy, setMetadataBusy] = useState(false);
  const [metadataError, setMetadataError] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const [history, setHistory] = useState<Operation[]>([]);
  const [plan, setPlan] = useState<ChangePlan>();
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState('');
  const [structuredOpen, setStructuredOpen] = useState(false);
  const [structuredText, setStructuredText] = useState('');
  const [managerMode, setManagerMode] = useState<'create' | 'relations' | 'archived'>();
  const [hiddenResultIds, setHiddenResultIds] = useState<string[]>([]);
  const [historicalResult, setHistoricalResult] = useState<QueryResult>();
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameTitle, setRenameTitle] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const conversationSequence = useRef(0);
  const resultGeneration = useRef(0);
  const chatEnd = useRef<HTMLDivElement>(null);
  const selectedTable = catalog.tables.find(table => table.id === (routeTableId || selectedTableId));
  const tableLabel = (table?: DataTable) => {
    if (!table) return '数据结果';
    // 文件名相同仍是不同的数据表；只在显示名称重复时补充 ID，不改变真实表名或查询范围。
    const duplicateName = [...catalog.tables, ...(catalog.archived_tables || [])].some(item => item.id !== table.id && item.name === table.name);
    return duplicateName ? `${table.name}（${table.id.slice(0, 8)}）` : table.name;
  };
  const conversationLabel = (item: Conversation) => item.last_question === undefined ? item.title : item.last_question?.trim().slice(0, 80) || '新对话';

  // 路由页面只切换中间内容；工作台保持挂载，保留右侧对话、草稿和正在运行的任务。
  useEffect(() => {
    if (routeTableId) { setSelectedTableId(routeTableId); setScopeTableIds([routeTableId]); setSelectedRelation(undefined); }
  }, [routeTableId]);
  useEffect(() => { if (routeConversationId) setConversationId(routeConversationId); }, [routeConversationId]);

  const refreshCatalog = useCallback(async () => {
    const next = await api<Catalog>('/catalog');
    setCatalog({ ...blankCatalog, ...next });
    setHiddenResultIds(next.hidden_result_ids || []);
    setSelectedTableId(current => current && next.tables.some(table => table.id === current) ? current : next.tables[0]?.id);
  }, []);
  const loadConversation = useCallback(async (id: string) => {
    const sequence = ++conversationSequence.current;
    const generation = resultGeneration.current;
    const next = await api<ConversationDetail>(`/conversations/${id}`);
    // 异步会话切换用序号避免较慢请求覆盖用户后来选择的会话。
    if (sequence === conversationSequence.current) setConversation(next);
    if (next.hidden_result_ids) setHiddenResultIds(next.hidden_result_ids);
    if (generation === resultGeneration.current) setResults(current => retainResults(current, next.results || []));
    setConversations(current => current.map(item => item.id === id ? { ...item, last_question: next.messages.filter(item => item.role === 'user').at(-1)?.content || null } : item));
  }, []);
  const onAgentResponse = useCallback(async (response: ChatResponse, id: string) => {
    if (response.result) setResults(current => retainResults(current, [response.result!]));
    if (response.plan) { setPlanError(''); setPlan(response.plan); }
    await loadConversation(id); await refreshCatalog();
    const listing = await api<{ conversations: Conversation[] }>('/conversations'); setConversations(listing.conversations);
  }, [loadConversation, refreshCatalog]);
  const onAgentSettled = useCallback(async (id: string) => { await loadConversation(id); }, [loadConversation]);
  const agent = useAgentRuns(conversationId, onAgentResponse, onAgentSettled);
  const chatBusy = agent.active || agent.starting;
  const previousAgentError = useRef('');
  useEffect(() => {
    const previous = previousAgentError.current; previousAgentError.current = agent.error;
    // 只清除由任务状态产生的旧提示，避免队列恢复时抹掉用户编辑或改名的其他错误。
    setChatError(current => agent.error || (current === previous ? '' : current));
  }, [agent.error]);
  useEffect(() => {
    setScopeTableIds(current => {
      const valid = current.filter(id => catalog.tables.some(table => table.id === id));
      return valid.length ? valid : selectedTableId ? [selectedTableId] : [];
    });
  }, [catalog.tables, selectedTableId]);
  useEffect(() => {
    if (!selectedRelation) return;
    const left = catalog.tables.find(table => table.id === selectedRelation.left_table_id);
    const right = catalog.tables.find(table => table.id === selectedRelation.right_table_id);
    if (!left || !right || (selectedRelation.left_version !== undefined && left.version !== selectedRelation.left_version) || (selectedRelation.right_version !== undefined && right.version !== selectedRelation.right_version)) {
      setSelectedRelation(undefined); message.warning('所选关联的表版本已变化，请在“管理关联”中重新预览确认。');
    }
  }, [catalog.tables, selectedRelation, message]);
  useEffect(() => {
    let cancelled = false;
    void api<{ relations: TableRelation[] }>('/relations').then(response => { if (!cancelled) setRelations(response.relations); })
      .catch(failure => { if (!cancelled) setGlobalError(`关联规则加载失败：${errorText(failure)}`); });
    return () => { cancelled = true; };
  }, [catalog.tables]);

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      try {
        const [, response] = await Promise.all([refreshCatalog(), api<Conversation[] | { conversations: Conversation[] }>('/conversations')]);
        let list = Array.isArray(response) ? response : response.conversations;
        if (!list.length) { const created = await api<Conversation>('/conversations', { method: 'POST', body: jsonBody({ title: '我的数据对话' }) }); list = [created]; }
        if (cancelled) return;
        setConversations(list);
        const saved = localStorage.getItem(`vibe:${user.id}:conversation`);
        setConversationId(routeConversationId || list.find(c => c.id === saved)?.id || list[0]?.id);
        // 恢复账号的服务器快照，让重登后的工作台继续显示已保存结果。
        const restoreGeneration = resultGeneration.current;
        const details = await Promise.allSettled(list.map(item => api<ConversationDetail>(`/conversations/${item.id}`)));
        if (!cancelled && restoreGeneration === resultGeneration.current) {
          const restored = details.flatMap(item => item.status === 'fulfilled' ? item.value.results || [] : []);
          setResults(current => retainResults(current, restored));
          const failed = details.find(item => item.status === 'rejected');
          if (failed?.status === 'rejected') setGlobalError(`部分对话恢复失败，可刷新重试：${errorText(failed.reason)}`);
        }
      } catch (failure) { if (!cancelled) setGlobalError(errorText(failure)); }
      finally { if (!cancelled) setLoading(false); }
    };
    void initialize();
    return () => { cancelled = true; };
  }, [refreshCatalog, user.id]);

  useEffect(() => {
    if (!conversationId) return;
    localStorage.setItem(`vibe:${user.id}:conversation`, conversationId);
    setChatError(''); setConversation(undefined);
    void loadConversation(conversationId).catch(failure => setChatError(errorText(failure)));
  }, [conversationId, loadConversation, user.id]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }, [conversation?.messages.length]);
  useEffect(() => {
    if (!catalog.tasks.some(task => ['pending', 'running'].includes(task.status))) return;
    const timer = setInterval(() => { void refreshCatalog().catch(failure => setGlobalError(errorText(failure))); }, 3000);
    return () => clearInterval(timer);
  }, [catalog.tasks, refreshCatalog]);

  const resize = (side: Side, value: number) => { (side === 'left' ? setLeftWidth : setRightWidth)(value); saveWidth(side, user.id, value); };
  const refresh = async () => { setGlobalError(''); try { await refreshCatalog(); if (conversationId) await loadConversation(conversationId); } catch (failure) { setGlobalError(errorText(failure)); } };
  const upload = async (file?: File) => {
    if (!file) return;
    const extension = file.name.split('.').pop()?.toLowerCase();
    if (!['xlsx', 'csv', 'docx', 'pdf'].includes(extension || '')) { message.error('第一版支持 xlsx / csv / docx / 文本 PDF；xls 请先转换为 xlsx。'); return; }
    setUploadBusy(true); setGlobalError('');
    const form = new FormData(); form.append('file', file);
    try {
      const response = await api<{ task_id: string; file_id: string }>('/files', { method: 'POST', body: form });
      setImportTaskId(response.task_id); setImportOpen(true); await refreshCatalog();
    } catch (failure) { setGlobalError(`上传失败：${errorText(failure)}。文件选择已保留，可再次上传。`); }
    finally { setUploadBusy(false); }
  };
  const newConversation = async () => {
    try { const created = await api<Conversation>('/conversations', { method: 'POST', body: jsonBody({ title: '新的数据对话' }) }); setConversations(current => [{ ...created, last_question: null }, ...current]); selectConversation(created.id); }
    catch (failure) { setChatError(errorText(failure)); }
  };
  const sendMessage = async (structuredPlan?: unknown) => {
    if (!conversationId || chatBusy || (!composer.trim() && !structuredPlan)) return;
    setChatError('');
    const sentDraft = composer;
    const accepted = await agent.start(structuredPlan ? '执行用户核对的结构化计划' : composer.trim(), scopeTableIds, structuredPlan);
    if (accepted) {
      // 异步提交期间也允许继续输入；仅清空仍与已发送文本相同的草稿。
      if (!structuredPlan) setComposer(current => current === sentDraft ? '' : current);
      setStructuredOpen(false);
    }
  };
  const selectTable = (id: string) => { setSelectedTableId(id); setScopeTableIds([id]); setSelectedRelation(undefined); navigate(`/tables/${encodeURIComponent(id)}`); };
  const selectConversation = (id: string) => { setConversationId(id); navigate(`/conversations/${encodeURIComponent(id)}`); };
  const renameConversation = async () => {
    if (!conversationId || !renameTitle.trim()) return;
    setRenameBusy(true); setChatError('');
    try { await api(`/conversations/${conversationId}`, { method: 'PATCH', body: jsonBody({ title: renameTitle.trim() }) }); setConversations(current => current.map(item => item.id === conversationId ? { ...item, title: renameTitle.trim() } : item)); setConversation(current => current ? { ...current, title: renameTitle.trim() } : current); setRenameOpen(false); }
    catch (failure) { setChatError(`对话名称保存失败：${errorText(failure)}。名称草稿已保留。`); }
    finally { setRenameBusy(false); }
  };
  const showPlan = async (id: string) => { setPlanError(''); try { setPlan(await api<ChangePlan>(`/plans/${id}`)); } catch (failure) { setChatError(errorText(failure)); } };
  const followUpPlan = () => {
    if (!plan) return;
    // 追问必须回到保存计划的会话和表范围，避免切表或慢请求后修改了另一个上下文。
    if (plan.conversation_id) selectConversation(plan.conversation_id);
    if (plan.table_id) {
      const joinedTables = plan.operation === 'select' ? plan.after.flatMap(snapshot =>
        typeof snapshot.values.right_table_id === 'string' ? [snapshot.values.right_table_id] : []) : [];
      setSelectedTableId(plan.table_id);
      setScopeTableIds([...new Set([plan.table_id, ...joinedTables])]);
    }
    setSelectedRelation(undefined);
    setPlan(undefined);
    setComposer(current => current || '请修改上一计划：');
    window.setTimeout(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="向数据助手提问"]')?.focus(), 100);
  };
  const actOnPlan = async (action: 'confirm' | 'cancel') => {
    if (!plan) return; setPlanBusy(true); setPlanError('');
    try {
      const response = await api<{ plan: ChangePlan; result?: QueryResult; deleted_table_id?: string }>(`/plans/${plan.id}/${action}`, { method: 'POST' });
      if (response.deleted_table_id) {
        // 服务器已清除历史副本，失效所有旧读取，防止慢请求把删除前的快照重新显示出来。
        resultGeneration.current += 1; conversationSequence.current += 1;
        setResults({}); setHistoricalResult(undefined); setHistory([]); setSelectedRelation(undefined);
        setScopeTableIds(current => current.filter(id => id !== response.deleted_table_id)); navigate('/');
      }
      if (response.result) setResults(current => retainResults(current, [response.result!]));
      if (response.plan) setPlan(response.plan); else setPlan(undefined);
      message.success(action === 'confirm' ? '操作已执行，结果和审计已保存。' : '计划已取消。');
      await refreshCatalog(); if (conversationId) await loadConversation(conversationId);
      if (action === 'confirm' && plan.operation === 'create_table' && response.plan?.table_id) selectTable(response.plan.table_id);
    } catch (failure) { setPlanError(`${errorText(failure)}。请重新提问生成最新预览。`); }
    finally { setPlanBusy(false); }
  };
  const saveMetadata = async () => {
    if (!metadata) return; setMetadataBusy(true); setMetadataError('');
    try { await api(`/tables/${metadata.id}`, { method: 'PATCH', body: jsonBody({ version: metadata.version, name: metadata.name, description: metadata.description, description_provenance: metadata.description_provenance, fields: metadata.fields }) }); message.success('表说明与字段元数据已保存。'); await refreshCatalog(); setMetadata(undefined); }
    catch (failure) { setMetadataError(`${errorText(failure)}。编辑内容已保留，可修正后重试。`); }
    finally { setMetadataBusy(false); }
  };
  const openMetadata = (table: DataTable) => { setMetadataError(''); setMetadata(structuredClone(table)); };
  const receivePlan = (next: ChangePlan) => { setPlanError(''); setPlan(next); };
  const previewDelete = async (table: DataTable) => {
    if (!conversationId) { setGlobalError('请等待对话加载完成后重试。'); return; }
    try {
      const response = await api<{ plan: ChangePlan }>(`/tables/${table.id}/delete/preview`, { method: 'POST', body: jsonBody({ conversation_id: conversationId, version: table.version }) });
      receivePlan(response.plan);
    } catch (failure) { setGlobalError(`删除预览未生成：${errorText(failure)}`); }
  };
  const exportTable = async () => {
    if (!selectedTableId) return;
    try { await api(`/tables/${selectedTableId}/exports`, { method: 'POST' }); message.info('导出已加入后台任务，完成后在任务列表下载。'); await refreshCatalog(); }
    catch (failure) { setGlobalError(errorText(failure)); }
  };
  const retryTask = async (taskId: string) => { try { await api(`/tasks/${taskId}/retry`, { method: 'POST' }); await refreshCatalog(); } catch (failure) { setGlobalError(errorText(failure)); } };
  const openHistory = async () => { setHistoryOpen(true); try { const response = await api<{ operations: Operation[] }>('/history?limit=100'); setHistory(response.operations); } catch (failure) { setGlobalError(errorText(failure)); } };
  const resultVisibility = async (id: string, hide: boolean) => {
    try {
      await api(`/results/${id}/visibility`, { method: hide ? 'DELETE' : 'POST' });
      setHiddenResultIds(current => hide ? [...new Set([...current, id])] : current.filter(item => item !== id));
      message.success(hide ? '已隐藏页面显示；结果与操作记录仍保存在服务器。' : '结果已恢复显示。');
      await refreshCatalog(); if (conversationId) await loadConversation(conversationId);
    } catch (failure) { setGlobalError(`结果显示状态保存失败：${errorText(failure)}`); }
  };
  const viewHistoricalResult = async (id: string) => { try { setHistoricalResult(await api<QueryResult>(`/results/${id}`)); } catch (failure) { setGlobalError(errorText(failure)); } };
  const operationResultId = (operation: Operation): string | undefined => {
    const details = operation.details && typeof operation.details === 'object' ? operation.details as Record<string, unknown> : {};
    return typeof details.result_id === 'string' ? details.result_id : typeof operation.result_id === 'string' ? operation.result_id : undefined;
  };
  const openStructured = () => {
    const relationFields = selectedRelation ? [...(catalog.tables.find(table => table.id === selectedRelation.left_table_id)?.fields || []).map(field => `left__${field.id}`), ...(catalog.tables.find(table => table.id === selectedRelation.right_table_id)?.fields || []).map(field => `right__${field.id}`)] : undefined;
    setStructuredText(JSON.stringify({ operation: 'select', table_id: selectedRelation?.left_table_id || selectedTableId || '', ...(selectedRelation ? { relation_id: selectedRelation.id } : {}), fields: relationFields || selectedTable?.fields.map(field => field.id) || [], filters: [], sort: [], group_by: [], aggregates: [], limit: 10, chart: { type: 'none' } }, null, 2)); setStructuredOpen(true);
  };
  const executeStructured = () => { try { const parsed: unknown = JSON.parse(structuredText); void sendMessage(parsed); } catch (failure) { setChatError(`计划不是有效 JSON：${errorText(failure)}`); } };
  const imported = (tableIds?: string[]) => {
    void refreshCatalog().then(() => {
      if (tableIds?.length) { selectTable(tableIds[0]); setManagerMode('relations'); }
    }).catch(failure => setGlobalError(errorText(failure)));
  };

  return <div className="workspace-shell">
    <header className="topbar"><div className="brand"><span className="brand-icon"><DatabaseOutlined /></span><span>vibe<span className="brand-sub">数据智能体</span></span><Tag className="version-tag">V1 · 内网工作空间</Tag></div><Space wrap><Tooltip title="后台保存，按账号隔离"><Badge status="success" text="服务器存储" /></Tooltip><Button icon={<HistoryOutlined />} onClick={() => void openHistory()}>操作历史</Button><Avatar size="small" src={user.avatar_url || undefined} icon={<UserOutlined />} /><span>{user.display_name || user.username}</span><Button type="text" aria-label="退出登录" icon={<LogoutOutlined />} onClick={() => void onLogout().catch(failure => setGlobalError(errorText(failure)))} /></Space></header>
    {globalError && <Alert className="workspace-error" type="error" showIcon closable message="操作失败" description={globalError} onClose={() => setGlobalError('')} action={<Button size="small" onClick={() => void refresh()}>重试加载</Button>} />}
    <div className="workspace-grid" style={{ gridTemplateColumns: `${leftWidth}px 8px minmax(280px, 1fr) 8px ${rightWidth}px` }}>
      <aside className="catalog-sidebar"><div className="section-eyebrow">WORKSPACE MENU</div><div className="sidebar-heading"><h2>工作空间</h2></div>
        <nav className="workspace-navigation" aria-label="主菜单">
          <Link className={`menu-link ${location.pathname === '/' ? 'active' : ''}`} to="/"><DatabaseOutlined />首页</Link>
          <Link className={`menu-link ${location.pathname.startsWith('/tables') ? 'active' : ''}`} to={selectedTableId ? `/tables/${selectedTableId}` : '/tables'}><FileTextOutlined />全部数据表</Link>
          <label className="form-label">全部数据表<Select aria-label="全部数据表" showSearch optionFilterProp="label" value={selectedTable?.id} placeholder="选择数据表" onChange={selectTable} options={catalog.tables.map(table => ({ value: table.id, label: tableLabel(table) }))} optionRender={option => <span className="table-option-label">{option.label}</span>} /></label>
          <label className="form-label"><HistoryOutlined /> 最近对话<Select aria-label="最近对话" placeholder="选择最近提问" value={conversationId} onChange={selectConversation} options={conversations.map(item => ({ value: item.id, label: conversationLabel(item) }))} optionRender={option => <span className="table-option-label">{option.label}</span>} /></label>
          <Button icon={<PlusOutlined />} onClick={() => void newConversation()}>新建对话</Button>
          <Link className={`menu-link ${location.pathname === '/profile' ? 'active' : ''}`} to="/profile"><UserOutlined />用户个人主页</Link>
        </nav>
        <input className="visually-hidden" type="file" accept=".xlsx,.csv,.docx,.pdf" ref={uploadInput} onChange={e => void upload(e.target.files?.[0])} aria-label="选择数据文件" />
        <div className="sidebar-footer"><span>数据与访问权限</span><p>只展示当前账号的数据表与对话。表、记录和关联操作均可核对影响范围。</p><Button type="text" size="small" icon={<ReloadOutlined />} onClick={() => void refresh()}>刷新目录</Button><Button type="text" size="small" onClick={() => setManagerMode('archived')}>已归档表</Button></div>
      </aside>
      <ResizableSeparator side="left" width={leftWidth} onChange={value => resize('left', value)} />
      <main className="workbench">
        <Routes>
          <Route path="/" element={<HomePage tables={catalog.tables} relations={relations} loading={loading || uploadBusy} tableLabel={tableLabel} onOpen={selectTable} onCreate={() => setManagerMode('create')} onImport={() => uploadInput.current?.click()} onEdit={openMetadata} onDelete={table => void previewDelete(table)} onRelations={() => setManagerMode('relations')} />} />
          <Route path="/tables" element={<TablePage table={selectedTable} tables={catalog.tables} tableLabel={tableLabel} conversationId={conversationId} onSelect={selectTable} onEdit={openMetadata} onDelete={table => void previewDelete(table)} onRelations={() => setManagerMode('relations')} onExport={() => void exportTable()} onPlan={receivePlan} />} />
          <Route path="/tables/:tableId" element={<TablePage table={selectedTable} tables={catalog.tables} tableLabel={tableLabel} conversationId={conversationId} onSelect={selectTable} onEdit={openMetadata} onDelete={table => void previewDelete(table)} onRelations={() => setManagerMode('relations')} onExport={() => void exportTable()} onPlan={receivePlan} />} />
          <Route path="/conversations/:conversationId" element={<ConversationPage conversation={conversation} tables={[...catalog.tables, ...(catalog.archived_tables || [])]} hiddenResultIds={hiddenResultIds} tableLabel={tableLabel} onVisibility={(id, hide) => void resultVisibility(id, hide)} onView={id => void viewHistoricalResult(id)} />} />
          <Route path="/profile" element={<ProfilePage user={user} onUpdated={onUserUpdated} onPasswordChanged={onPasswordChanged} />} />
          <Route path="*" element={<Navigate replace to="/" />} />
        </Routes>
        {location.pathname === '/' && catalog.tasks.length > 0 && <Card className="block-gap" title="导入与后台任务" size="small"><div className="task-list">{catalog.tasks.map(task => <div className="task-row" key={task.id}><div><Tag color={taskColors[task.status]}>{taskLabels[task.status]}</Tag><span>{task.kind === 'export' ? 'CSV 导出' : '文件解析'}</span>{task.error && <p className="task-error">{task.error}</p>}</div><Space wrap>{task.status === 'failed' && <Button size="small" onClick={() => void retryTask(task.id)}>重试</Button>}{task.kind !== 'export' && task.status !== 'failed' && <Button size="small" onClick={() => { setImportTaskId(task.id); setImportOpen(true); }}>核对导入</Button>}{task.kind === 'export' && task.status === 'completed' && <Button size="small" href={downloadUrl(`/tasks/${task.id}/download`)}>下载</Button>}</Space></div>)}</div></Card>}
      </main>
      <ResizableSeparator side="right" width={rightWidth} onChange={value => resize('right', value)} />
      <aside className="assistant-sidebar"><div className="assistant-header"><span className="assistant-avatar"><RobotOutlined /></span><div><h2>数据助手</h2><small>有依据的回答，可追溯的操作</small></div><Tag color={catalog.model_mode === 'disabled' ? 'default' : catalog.model_mode === 'mock' ? 'orange' : 'green'}>{catalog.model_mode === 'mock' ? '测试替身' : catalog.model_mode === 'disabled' ? '模型未配置' : '模型已配置'}</Tag></div>
        <div className="conversation-select"><Select aria-label="选择对话" placeholder="加载对话…" value={conversationId} onChange={selectConversation} options={conversations.map(item => ({ value: item.id, label: conversationLabel(item) }))} /><Tooltip title="修改对话名称"><Button aria-label="修改对话名称" disabled={!conversationId} icon={<EditOutlined />} onClick={() => { setRenameTitle(conversation?.title || conversations.find(item => item.id === conversationId)?.title || ''); setRenameOpen(true); }} /></Tooltip><Tooltip title="新建对话"><Button aria-label="新建对话" icon={<PlusOutlined />} onClick={() => void newConversation()} /></Tooltip></div>
        <div className="assistant-context"><DatabaseOutlined /><div>当前提问范围<Select mode="multiple" aria-label="提问数据表范围" placeholder="选择一张或多张表" value={scopeTableIds} options={catalog.tables.map(table => ({ value: table.id, label: tableLabel(table) }))} onChange={value => { setScopeTableIds(value); setSelectedRelation(undefined); if (value[0]) setSelectedTableId(value[0]); }} />{selectedRelation && <Tag closable onClose={() => setSelectedRelation(undefined)}>已选关联 {selectedRelation.id?.slice(0, 8)}</Tag>}</div></div>
        <div className="messages-scroll">
          {catalog.model_mode === 'disabled' && <Alert showIcon type="info" message="中转站尚未配置" description="中文模型调用暂不可用。可使用底部“结构化计划”执行真实数据库查询与变更预览；管理员配置并验证接口后再启用模型。" />}
          {catalog.model_mode === 'mock' && <Alert showIcon type="warning" message="确定性测试替身" description="当前为本地验证模式，没有调用真实模型。可提问：查看前10条、按班级统计平均成绩、修改学号为001的成绩为90。" />}
          {!conversation?.messages.length && <div className="assistant-intro"><h3>从一个清楚的问题开始</h3><p>可以查询、分组统计和提出变更。占比问题请说明阈值与分母，变更始终先预览。</p><div className="example-questions">{['查看前10条', '按班级统计平均成绩', '按班级统计成绩达标率，阈值80，分母全部记录'].map(question => <button key={question} onClick={() => setComposer(question)}>{question}<ArrowRightOutlined /></button>)}</div></div>}
          {conversation?.messages.map(chatMessage => <article key={chatMessage.id} className={`chat-message ${chatMessage.role}`}><div className="message-role">{chatMessage.role === 'user' ? <UserOutlined /> : <RobotOutlined />}<span>{chatMessage.role === 'user' ? user.display_name || user.username : '数据助手'}</span></div><div className="message-content">{chatMessage.content}</div>{chatMessage.clarifications?.length ? <Alert type="info" showIcon message="请补充口径" description={<ul>{chatMessage.clarifications.map((item, i) => <li key={i}>{item}</li>)}</ul>} /> : null}{chatMessage.result_id && !hiddenResultIds.includes(chatMessage.result_id) && results[chatMessage.result_id] && <><ResultView compact result={results[chatMessage.result_id]} /><Button size="small" type="text" aria-label={`助手隐藏结果 ${chatMessage.result_id}`} onClick={() => void resultVisibility(chatMessage.result_id!, true)}>隐藏此结果显示</Button></>}{chatMessage.result_id && hiddenResultIds.includes(chatMessage.result_id) && <Typography.Paragraph type="secondary">该结果已隐藏，服务器快照与操作记录仍保留。<Button size="small" type="link" onClick={() => void resultVisibility(chatMessage.result_id!, false)}>恢复此结果显示</Button></Typography.Paragraph>}{chatMessage.result_id && !hiddenResultIds.includes(chatMessage.result_id) && !results[chatMessage.result_id] && <Button onClick={() => void api<QueryResult>(`/results/${chatMessage.result_id}`).then(result => setResults(current => retainResults(current, [result]))).catch(failure => setChatError(errorText(failure)))}>重新加载结果快照</Button>}{chatMessage.plan_id && <Button className="block-gap" icon={<SettingOutlined />} onClick={() => void showPlan(chatMessage.plan_id!)}>查看操作预览与状态</Button>}</article>)}
          {chatBusy && <div className="assistant-busy" data-run-id={agent.run?.id}><Spin size="small" /> {agent.run?.status === 'queued' ? '提问已保存，等待助手处理…' : '正在理解问题并校验操作预览…'} 可继续输入下一问题。</div>}{agent.run?.status === 'cancelled' && <Alert type="info" message="本次提问已中止，没有执行未确认的操作。" />}<div ref={chatEnd} />
        </div>
        <div className="composer">{chatError && <Alert type="error" showIcon closable message="发送未完成" description={chatError} onClose={() => setChatError('')} />}<Input.TextArea aria-label="向数据助手提问" placeholder={selectedTable ? '用中文提问，助手会先说明下一步操作' : '先导入或新建数据表'} value={composer} onChange={e => setComposer(e.target.value)} autoSize={{ minRows: 3, maxRows: 6 }} onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (!chatBusy) void sendMessage(); } }} /><div className="composer-actions"><Button type="text" size="small" onClick={openStructured} disabled={!selectedTable || chatBusy}>结构化计划</Button>{chatBusy ? <Button danger type="primary" icon={<StopOutlined />} loading={agent.cancelling} disabled={agent.starting} onClick={() => void agent.cancel()}>中止</Button> : <Button type="primary" icon={<SendOutlined />} disabled={!composer.trim() || !scopeTableIds.length || !conversationId} onClick={() => void sendMessage()}>发送</Button>}</div><small>Ctrl + Enter 发送 · 查询与变更均先预览，再确认执行</small></div>
      </aside>
    </div>

    <ImportDrawer open={importOpen} taskId={importTaskId} onClose={() => setImportOpen(false)} onImported={imported} />
    <PlanModal plan={plan} table={[...catalog.tables, ...(catalog.archived_tables || [])].find(table => table.id === plan?.table_id)} busy={planBusy} error={planError} onClose={() => setPlan(undefined)} onConfirm={() => void actOnPlan('confirm')} onCancel={() => void actOnPlan('cancel')} onFollowUp={followUpPlan} />
    <TableManager mode={managerMode} tables={[...catalog.tables, ...(catalog.archived_tables || [])]} tableId={selectedTableId} conversationId={conversationId} onClose={() => setManagerMode(undefined)} onPlan={next => { setPlanError(''); setPlan(next); }} onRelation={relation => { setSelectedRelation(relation); setScopeTableIds([relation.left_table_id, relation.right_table_id]); setSelectedTableId(relation.left_table_id); setComposer(current => current || '请通过已确认关联联表查看前10条记录，先预览操作。'); }} />
    <Modal title="修改对话名称" open={renameOpen} onCancel={() => setRenameOpen(false)} okText="保存名称" okButtonProps={{ disabled: !renameTitle.trim() }} confirmLoading={renameBusy} onOk={() => void renameConversation()}><label className="form-label">对话名称<Input aria-label="对话名称" maxLength={100} value={renameTitle} onChange={event => setRenameTitle(event.target.value)} onPressEnter={() => void renameConversation()} /></label><Typography.Text type="secondary">名称保存到服务器，刷新与重新登录后保留。</Typography.Text>{chatError && <Alert className="block-gap" type="error" message={chatError} />}</Modal>
    <Modal title="操作历史中的保留快照" open={Boolean(historicalResult)} onCancel={() => setHistoricalResult(undefined)} footer={<Button onClick={() => setHistoricalResult(undefined)}>关闭</Button>} width={960}>{historicalResult && <><Alert type="info" message="查看服务器保存的操作结果，不会自动恢复页面显示。" /><div className="block-gap"><ResultView result={historicalResult} /></div></>}</Modal>
    <Drawer title="编辑表与字段说明" open={Boolean(metadata)} onClose={() => setMetadata(undefined)} width={1000} extra={<Button type="primary" loading={metadataBusy} onClick={() => void saveMetadata()}>保存说明</Button>}>
      {metadata && <><Alert type="info" showIcon message={`基于表版本 ${metadata.version} 编辑`} description="更改字段类型会校验已有数据。版本发生变化时保存将被拒绝；编辑草稿保留以便核对重试。" />{metadataError && <Alert className="block-gap" showIcon type="error" message="保存失败" description={metadataError} />}<label className="form-label">表名<Input value={metadata.name} onChange={e => setMetadata({ ...metadata, name: e.target.value })} /></label><label className="form-label">表说明<Input.TextArea value={metadata.description} onChange={e => setMetadata({ ...metadata, description: e.target.value, description_provenance: 'user' })} /></label><label className="form-label">表说明来源<Select aria-label="表说明来源" value={metadata.description_provenance || 'file'} options={[{value:'file',label:'文件原文'},{value:'user',label:'用户输入'},{value:'confirmed_inference',label:'用户确认的推断'}]} onChange={value => setMetadata({...metadata, description_provenance:value})} /></label><FieldEditor fields={metadata.fields} onChange={fields => setMetadata({ ...metadata, fields })} /><Collapse className="block-gap" items={[{ key: 'source', label: '源文件与表标识', children: <pre className="json-preview">{JSON.stringify({ table_id: metadata.id, file_id: metadata.file_id, source: metadata.source }, null, 2)}</pre> }]} /></>}
    </Drawer>
    <Drawer title="操作历史与审计" open={historyOpen} onClose={() => setHistoryOpen(false)} width={880}><Table size="small" rowKey="id" dataSource={history} scroll={{ x: 700 }} pagination={{ pageSize: 10 }} columns={[{ title: '时间', dataIndex: 'created_at', width: 180, render: value => value ? new Date(value).toLocaleString('zh-CN') : '—' }, { title: '操作', render: (_, item) => item.action || item.operation || '操作', width: 140 }, { title: '表', dataIndex: 'table_id', render: id => [...catalog.tables, ...(catalog.archived_tables || [])].find(t => t.id === id)?.name || id || '—' }, { title: '详细审计', render: (_, item) => <><pre className="source-cell">{JSON.stringify(item.details ?? item, null, 2)}</pre>{operationResultId(item) && <Button size="small" onClick={() => void viewHistoricalResult(operationResultId(item)!)}>查看操作结果</Button>}</> }]} locale={{ emptyText: <Empty description="暂无操作记录" /> }} /></Drawer>
    <Modal open={structuredOpen} title="用户结构化计划" width={850} onCancel={() => setStructuredOpen(false)} okText="校验并生成预览" confirmLoading={chatBusy} onOk={executeStructured}>
      <Alert type="info" showIcon message="直接提供计划，仍由服务器完整校验" description="无需模型即可验证真实数据库查询。仅支持登记的逻辑表 / 字段 ID，禁止 SQL、代码或任意工具。查询、新增、修改、删除和字段说明均先生成预览，确认后才执行。" />
      <div className="field-reference block-gap">当前字段：{(selectedRelation?.fields || selectedTable?.fields)?.map(field => <Tag key={field.id}>{field.name} = {field.id}</Tag>)}</div>
      <Input.TextArea className="plan-editor block-gap" aria-label="结构化查询计划 JSON" value={structuredText} onChange={e => setStructuredText(e.target.value)} rows={15} />
      {chatError && <Alert className="block-gap" type="error" message={chatError} />}
      <Collapse className="block-gap" items={[{ key: 'examples', label: '筛选、分组与变更语法', children: <pre className="json-preview">{`筛选: filters: [{"field_id":"字段ID","op":"gte","value":80}]
排序: sort: [{"field_id":"字段ID","direction":"desc"}]
分组: group_by: ["班级字段ID"]
统计: aggregates: [{"field_id":"成绩字段ID","function":"avg","alias":"average_score"}]
修改: operation: "update", values: {"成绩字段ID":90}, filters: [明确范围]
删除: operation: "delete", filters: [明确范围]
新增: operation: "insert", values: {"登记字段ID":"新值"}
占比: ratio: {"field_id":"成绩字段ID","threshold":80,"denominator":"all"}`}</pre> }]} />
    </Modal>
  </div>;
}
