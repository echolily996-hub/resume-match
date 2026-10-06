export interface User { id: string; username: string }
export interface SessionInfo { user: User; csrf_token: string }
export interface ResumeSummary { id: string; title: string; created_at: string }
export interface Resume extends ResumeSummary { original_text: string }
export interface JobSummary { id: string; name: string; version: number; created_at: string; updated_at: string }
export interface Job extends JobSummary { description: string }
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export type AnalysisState = 'queued' | 'running' | 'succeeded' | 'failed'
export interface AnalysisSummary {
  id: string; resume_id: string; job_id: string; job_name_snapshot: string
  state: AnalysisState; attempt_no: number; error_code: string | null
  error_message: string | null; created_at: string
}
export interface AnalysisRequirement {
  id: string; ordinal: number; job_requirement_quote: string
  resume_evidence_quote: string | null
  match_status: 'reflected' | 'not_reflected' | 'needs_confirmation'
  reason: string; confirmation_questions: string[]
}
export type SuggestionDecision = 'pending' | 'accepted' | 'ignored'
export interface Suggestion {
  id: string; requirement_id: string; source_segment_id: string
  original_text: string; suggested_text: string | null; rewrite_reason: string
  validation_status: 'actionable' | 'blocked'; blocked_reason: string | null
  decision: SuggestionDecision; decided_at: string | null
}
export interface Draft {
  id: string; analysis_id: string; working_markdown: string
  edit_version: number; saved_version: number; saved_markdown: string | null
  created_at: string; updated_at: string
}
export interface SavedRevisionSummary {
  id: string; draft_id: string; version: number; created_at: string
}
export interface SavedRevision extends SavedRevisionSummary {
  body_markdown: string
  suggestion_decisions: Record<string, SuggestionDecision>
}
export interface SavedPreview {
  analysis_id: string; version: number; body_markdown: string; created_at: string
}
export interface Analysis extends AnalysisSummary {
  resume_snapshot: string; job_description_snapshot: string
  requirements: AnalysisRequirement[]; suggestions: Suggestion[]; warnings: unknown[]
  model_metadata: null | {
    model?: string; duration_ms?: number; response_status?: number
    provider_request_id?: string; attempt_no?: number
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code: string | null = null,
    public details: Record<string, unknown> | null = null,
  ) { super(message) }
}

async function responseFor(path: string, init: RequestInit = {}): Promise<Response> {
  let response: Response
  try {
    response = await fetch(`/api/v1${path}`, { ...init, credentials: 'same-origin' })
  } catch {
    throw new ApiError(0, '无法连接服务，请检查网络后重试。')
  }
  if (!response.ok) {
    const data = await response.json().catch(() => null)
    throw new ApiError(
      response.status,
      data?.error?.message ?? `服务请求失败（${response.status}）。`,
      data?.error?.code ?? null,
      data?.error?.details ?? null,
    )
  }
  return response
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await responseFor(path, init)
  if (response.status === 204) return undefined as T
  return response.json()
}

export const authApi = {
  me: () => request<SessionInfo>('/auth/me'),
  login: (username: string, password: string) => request<SessionInfo>('/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  }),
  logout: (csrf: string) => request<void>('/auth/logout', {
    method: 'POST', headers: { 'X-CSRF-Token': csrf },
  }),
}

function jsonMutation(method: string, csrf: string, body: unknown,
                      extraHeaders: Record<string, string> = {}): RequestInit {
  return {
    method,
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...extraHeaders},
    body: JSON.stringify(body),
  }
}

export const resumeApi = {
  list: () => request<Page<ResumeSummary>>('/resumes'),
  get: (id: string) => request<Resume>(`/resumes/${id}`),
  create: (csrf: string, title: string, original_text: string) =>
    request<Resume>('/resumes', jsonMutation('POST', csrf, {title, original_text})),
}

export const jobApi = {
  list: () => request<Page<JobSummary>>('/jobs'),
  get: (id: string) => request<Job>(`/jobs/${id}`),
  create: (csrf: string, name: string, description: string) =>
    request<Job>('/jobs', jsonMutation('POST', csrf, {name, description})),
  update: (csrf: string, id: string, name: string, description: string, expected_version: number) =>
    request<Job>(`/jobs/${id}`, jsonMutation('PUT', csrf, {name, description, expected_version})),
}

export const analysisApi = {
  list: () => request<Page<AnalysisSummary>>('/analyses'),
  get: (id: string) => request<Analysis>(`/analyses/${id}`),
  create: (csrf: string, resume_id: string, job_id: string, idempotencyKey: string) =>
    request<AnalysisSummary>('/analyses', jsonMutation(
      'POST', csrf, {resume_id, job_id}, {'Idempotency-Key': idempotencyKey})),
  retry: (csrf: string, id: string, expected_attempt_no: number) =>
    request<AnalysisSummary>(`/analyses/${id}/retry`,
      jsonMutation('POST', csrf, {expected_attempt_no})),
  draft: (id: string) => request<Draft>(`/analyses/${id}/draft`),
  saveDraft: (csrf: string, id: string, body_markdown: string, expected_edit_version: number) =>
    request<{draft: Draft; revision: SavedRevision}>(
      `/analyses/${id}/draft`,
      jsonMutation('PUT', csrf, {body_markdown, expected_edit_version})),
  revisions: (id: string) =>
    request<{items: SavedRevisionSummary[]; total: number}>(
      `/analyses/${id}/draft/revisions`),
  revision: (id: string, version: number) =>
    request<SavedRevision>(`/analyses/${id}/draft/revisions/${version}`),
  restoreRevision: (csrf: string, id: string, version: number,
                    expected_edit_version: number) =>
    request<Draft>(
      `/analyses/${id}/draft/revisions/${version}/restore`,
      jsonMutation('POST', csrf, {expected_edit_version})),
  preview: (id: string, version?: number) =>
    request<SavedPreview>(
      `/analyses/${id}/preview${version === undefined ? '' : `?version=${version}`}`),
  exportMarkdown: async (id: string, version?: number) => {
    const response = await responseFor(
      `/analyses/${id}/export.md${version === undefined ? '' : `?version=${version}`}`)
    const disposition = response.headers.get('Content-Disposition') ?? ''
    const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? 'resume.md'
    return {blob: await response.blob(), filename}
  },
  decide: (csrf: string, analysisId: string, suggestionId: string,
           decision: 'accepted' | 'ignored', draft?: Draft, workingMarkdown?: string) =>
    request<{suggestion: Suggestion; draft: Draft}>(
      `/analyses/${analysisId}/suggestions/${suggestionId}/decision`,
      jsonMutation('PUT', csrf, decision === 'accepted' ? {
        decision, working_markdown: workingMarkdown,
        expected_edit_version: draft?.edit_version,
      } : {decision})),
}
