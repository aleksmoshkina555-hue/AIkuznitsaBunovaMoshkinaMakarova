export type Role = 'Администратор' | 'Пользователь'

export interface User {
  login: string
  name: string
  role: Role
  active: boolean
  created: string
  last_login: string | null
  email?: string
  department?: string
}

export interface Doc {
  id: string
  source_type: string
  number?: string
  title: string
  date: string
  owner?: string
  status?: string
  status_reason?: string
  points?: string[]
  departments?: string[]
}

export interface AgentResult {
  blocked?: boolean
  reason?: string
  found?: boolean
  answer?: string
  answer_source?: string
  responsible?: string
  responsible_label?: string
  email_draft?: string
  basis_docs?: Doc[]
  active_docs?: Doc[]
  inactive_docs?: Doc[]
  conflicts?: string[]
  fact_issues?: string[]
  needs_clarification?: boolean
  clarify_by?: 'department' | 'amount' | null // что уточнить: подразделение или сумму договора
  clarify_options?: string[] // подразделения, для которых в регламентах есть ответ
  assignee?: string // кому передан вопрос: эксперт темы, владелец документа или дежурный методолог
  assignee_reason?: string // почему ему
  topic?: string | null // тема вопроса
  draft_saved_by?: string // кто сохранил правку черновика ответа (без отправки)
  draft_saved_at?: string
  escalation_reason?: string
  escalation_id?: string
  relevance?: string
  latency?: number
  tokens?: number
  logs?: string[]
  withheld?: { score: number; reasons: string[]; note: string; answer_source?: string }
  department?: string | null
  other_departments?: string[]
}

export interface Confidence {
  color: 'green' | 'yellow' | 'red' | 'hidden' | 'none'
  label: string
  score: number
  reasons: string[]
}

export interface Described {
  level: 'high' | 'checked' | 'document' | 'escalation' | 'blocked'
  label: string
  contacts: string
  escalation_contacts: string
  confidence?: Confidence
}

export interface RequestItem {
  id: string
  created: string
  due: string
  sender: string
  subject: string
  status: string
  read: boolean
  source: string
  process: string
  overdue: boolean
  closed: boolean
  sent_at: string | null
  preview: string
  assignee?: string
  body?: string
  result?: AgentResult | null
}

export interface Meta {
  processes: string[]
  source_groups: string[]
  smtp: boolean
  sla_hours: number
  escalation_contact: string
  model: string
  is_admin: boolean
  departments: string[]
  document_types?: string[]
  version?: string
  statuses: Record<string, string>
}
