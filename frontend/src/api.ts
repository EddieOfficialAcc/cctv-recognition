export type CheckStatus = 'pass' | 'flagged' | 'not_assessed'
export type ReviewStatus = 'new' | 'acknowledged' | 'false_positive'
export type Outcome = 'flagged' | 'needs_review'
export type Region = 'Hong Kong' | 'Macau'

export type ErrorCode = 'requestError' | 'storageError' | 'analysisError' | 'validationError' | 'notFoundError' |
  'responseError' | 'fileTypeError' | 'fileSizeError' | 'imageCorruptError' | 'resolutionError' | 'animatedError'

export class ApiError extends Error {
  readonly code: ErrorCode

  constructor(code: ErrorCode, message: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
  }
}

export interface RecognitionCheck {
  category: 'signal' | 'timestamp' | 'scene'
  status: CheckStatus
  explanation: string
}

export interface RecognitionRecord {
  id: string
  shop_code: string
  camera_name: string
  region: Region
  filename: string
  uploaded_at: string
  captured_at: string | null
  width: number
  height: number
  outcome: Outcome
  review_status: ReviewStatus
  review_note: string
  reviewed_at: string | null
  analyzer_version: string
  checks: RecognitionCheck[]
}

export interface Baseline {
  record_id: string
  approved_at: string
}

export interface RecognitionPage {
  records: RecognitionRecord[]
  total: number
  page: number
  page_size: number
  summary: { total: number; flagged: number; needs_review: number; unreviewed: number }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function isRecord(value: unknown): value is RecognitionRecord {
  return isObject(value) &&
    ['id', 'shop_code', 'camera_name', 'filename', 'uploaded_at', 'review_note', 'analyzer_version']
      .every(key => typeof value[key] === 'string') &&
    (value.region === 'Hong Kong' || value.region === 'Macau') &&
    (value.outcome === 'flagged' || value.outcome === 'needs_review') &&
    (value.review_status === 'new' || value.review_status === 'acknowledged' || value.review_status === 'false_positive') &&
    (value.captured_at === null || typeof value.captured_at === 'string') &&
    (value.reviewed_at === null || typeof value.reviewed_at === 'string') &&
    typeof value.width === 'number' && typeof value.height === 'number' &&
    Array.isArray(value.checks) && value.checks.length === 3 &&
    ['signal', 'timestamp', 'scene'].every(category =>
      value.checks instanceof Array && value.checks.filter(check =>
        isObject(check) && check.category === category &&
        ['pass', 'flagged', 'not_assessed'].includes(String(check.status)) &&
        typeof check.explanation === 'string',
      ).length === 1,
    )
}

function isPage(value: unknown): value is RecognitionPage {
  return isObject(value) && Array.isArray(value.records) && value.records.every(isRecord) &&
    ['total', 'page', 'page_size'].every(key => typeof value[key] === 'number') &&
    isObject(value.summary) && ['total', 'flagged', 'needs_review', 'unreviewed']
      .every(key => isObject(value.summary) && typeof value.summary[key] === 'number')
}

async function request<T>(
  url: string, validate: (value: unknown) => value is T, options?: RequestInit,
): Promise<T> {
  const response = await fetch(url, options)
  if (!response.ok) {
    let message = `Request failed (HTTP ${response.status}). Please try again.`
    if (response.headers.get('content-type')?.includes('application/json')) {
      const body: unknown = await response.json()
      if (isObject(body) && typeof body.detail === 'string') message = body.detail
      else if (response.status === 422) message = 'Check the screenshot and required fields, then try again.'
    }
    let code: ErrorCode = response.status === 503 ? 'storageError' :
      response.status === 502 ? 'analysisError' :
      response.status === 404 ? 'notFoundError' :
      response.status === 413 ? 'fileSizeError' :
      response.status === 422 ? 'validationError' : 'requestError'
    if (response.status === 422) {
      if (message.includes('supported')) code = 'fileTypeError'
      else if (message.includes('10 MB')) code = 'fileSizeError'
      else if (message.includes('corrupt')) code = 'imageCorruptError'
      else if (message.includes('20 megapixels')) code = 'resolutionError'
      else if (message.includes('animated')) code = 'animatedError'
    }
    throw new ApiError(code, message)
  }
  const body: unknown = await response.json()
  if (!validate(body)) throw new ApiError('responseError', 'The server returned an unexpected recognition response.')
  return body
}

export function fetchRecords(params: URLSearchParams, signal: AbortSignal) {
  return request(`/api/recognitions?${params}`, isPage, { signal })
}

export function uploadScreenshot(body: FormData) {
  return request('/api/recognitions', isRecord, { method: 'POST', body })
}

export function saveReview(id: string, status: ReviewStatus, note: string) {
  return request(`/api/recognitions/${id}/review`, isRecord, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, note }),
  })
}

export function evidenceUrl(id: string) {
  return `/api/recognitions/${id}/evidence`
}

export function burstFrameUrl(id: string, index: number) {
  return `/api/recognitions/${id}/burst/${index}`
}

export function fetchBurstInfo(id: string, signal: AbortSignal) {
  return request(`/api/recognitions/${id}/burst`, (value): value is { frame_count: number; duration_seconds: number } =>
    isObject(value) && typeof value.frame_count === 'number' && Number.isInteger(value.frame_count) &&
    value.frame_count >= 6 && value.frame_count <= 10 && typeof value.duration_seconds === 'number' &&
    value.duration_seconds >= 1.5 && value.duration_seconds <= 2, { signal })
}

function isBaseline(value: unknown): value is Baseline {
  return isObject(value) && typeof value.record_id === 'string' && typeof value.approved_at === 'string'
}

export function fetchBaseline(id: string) {
  return request(`/api/recognitions/${id}/baseline`, isBaseline)
}

export function approveBaseline(id: string) {
  return request(`/api/recognitions/${id}/baseline`, isBaseline, { method: 'POST' })
}

export const reviewKeys = {
  new: 'unreviewed', acknowledged: 'acknowledged', false_positive: 'falsePositive',
} as const satisfies Record<ReviewStatus, string>
export const outcomeKeys = {
  flagged: 'issueFlagged', needs_review: 'needsVerification',
} as const satisfies Record<Outcome, string>
export const checkKeys = {
  pass: 'noPattern', flagged: 'flagged', not_assessed: 'notAssessed',
} as const satisfies Record<CheckStatus, string>
export const regionKeys = {
  'Hong Kong': 'hongKong', Macau: 'macau',
} as const satisfies Record<Region, string>
export const categoryKeys = {
  signal: 'signalShort', timestamp: 'timeShort', scene: 'sceneShort',
} as const satisfies Record<RecognitionCheck['category'], string>
export const categoryTitleKeys = {
  signal: 'signalTitle', timestamp: 'timestampTitle', scene: 'sceneTitle',
} as const satisfies Record<RecognitionCheck['category'], string>
