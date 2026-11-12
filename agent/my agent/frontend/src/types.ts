/** API 共享数据类型。动态表仅使用服务端登记的字段 ID，结果由快照 ID 引用。 */
export type FieldType = 'string' | 'number' | 'integer' | 'boolean' | 'date';
export type Provenance = 'file' | 'user' | 'confirmed_inference';
export interface Field {
  id: string; name: string; type: FieldType; unit?: string; description?: string;
  aliases?: string[];
  provenance?: Provenance | Record<string, unknown>; source?: unknown;
  type_provenance?: 'file' | 'inference' | 'user' | 'confirmed_inference';
}
export interface DataRow { id?: string; values: Record<string, unknown>; source?: unknown }
export interface DataTable {
  id: string; name: string; description: string; version: number; row_count: number;
  fields: Field[]; file_id?: string; source?: unknown;
  archived?: boolean; created_at?: string;
  description_provenance?: Provenance; structure?: { grain?: string; relations?: { table_id: string; shared_field_ids: string[] }[] };
}
export interface Task {
  id: string; status: 'pending' | 'running' | 'completed' | 'failed'; kind: string;
  attempts?: number; error?: string; file_id?: string; created_at?: string;
}
export interface Operation {
  id: string; action?: string; operation?: string; table_id?: string; created_at?: string;
  details?: unknown; summary?: string; affected_count?: number; [key: string]: unknown;
}
export interface Catalog { tables: DataTable[]; tasks: Task[]; recent_operations: Operation[]; model_mode: string; archived_tables?: DataTable[]; hidden_result_ids?: string[] }
export interface User {
  id: string; username: string; csrf_token: string;
  display_name?: string; bio?: string; email?: string | null; avatar_url?: string | null;
}
export interface ChartSpec {
  type: 'bar' | 'line' | 'pie' | 'none'; x?: string; y?: string; unit?: string;
  title?: string; numerator?: string; denominator?: string; reason?: string;
}
export interface QueryResult {
  id: string; table_id: string; columns: Field[]; rows: Record<string, unknown>[];
  chart?: ChartSpec; explanation: string; query_snapshot: unknown; created_at?: string;
}
export interface PlanAssumption {
  subject: string; choice: string; reason: string; alternatives: string[];
}
export interface ChangePlan {
  id: string; status: string; operation: 'select' | 'insert' | 'update' | 'delete' | 'metadata' | 'relation' | 'create_table' | 'archive_table' | 'restore_table' | 'delete_table'; table_id: string | null;
  table_version: number; affected_count: number; before: DataRow[]; after: DataRow[]; expires_at: string;
  conversation_id?: string; intent?: string; query_snapshot?: unknown; plan?: unknown; assumptions?: PlanAssumption[];
}
export interface ChatMessage {
  id: string; role: 'user' | 'assistant'; content: string; result_id?: string; plan_id?: string;
  clarifications?: string[]; created_at?: string;
  table_ids?: string[];
}
export interface Conversation { id: string; title: string; created_at?: string; last_question?: string | null; updated_at?: string }
export interface ConversationDetail extends Conversation { messages: ChatMessage[]; results: QueryResult[]; hidden_result_ids?: string[] }
export interface ChatResponse { message: ChatMessage; result?: QueryResult; plan?: ChangePlan; clarifications?: string[] }
export interface AgentRun {
  id: string; status: 'queued' | 'running' | 'completed' | 'cancelled' | 'failed';
  message_id?: string; conversation_id: string; response?: ChatResponse; error?: string;
}
export interface TableRelation {
  id?: string; left_table_id: string; right_table_id: string; left_field_id: string; right_field_id: string;
  join_type: 'inner' | 'left'; reason?: string; status?: string;
  stale?: boolean; archived?: boolean;
  left_version?: number; right_version?: number; fields?: Field[];
}
export interface ImportCandidate {
  candidate_id: string; name: string; description: string; fields: Field[]; rows: DataRow[];
  original: unknown; warnings: string[]; requires_review: boolean; row_count: number;
  reviewed?: boolean; selected?: boolean; corrections?: Correction[];
  row_offset?: number; preview_limit?: number;
  description_provenance?: Provenance;
  selection_group?: string;
  mutually_exclusive_group?: string;
}
export interface Correction { row_index: number; field_id: string; value: unknown }
export interface ImportPreview { task_id: string; version: number; tables: ImportCandidate[]; warnings: string[] }
