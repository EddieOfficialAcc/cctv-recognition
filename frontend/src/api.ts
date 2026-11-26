import type { MessageKey } from './messages'

export type CheckStatus = 'pass' | 'flagged' | 'not_assessed'
export type ReviewStatus = 'new' | 'acknowledged' | 'false_positive'
export type Outcome = 'flagged' | 'needs_review'
export type Region = 'Hong Kong' | 'Macau'

export type ErrorCode = 'batchExclusionAmbiguous' | 'batchAlreadyRunning' | 'batchNoRecorders' | 'snapshotConfigurationError' | 'requestError' | 'storageError' | 'analysisError' | 'validationError' | 'notFoundError' |
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

export interface CameraConfiguration {
  shop_code: string | null
  shop_name: string | null
  company: string | null
  region: string | null
  brand: string | null
  internal_ip: string | null
  port: number | null
  cam_qty: number | null
  credentials_complete: boolean
}

export interface CameraConfigurationPage {
  rows: CameraConfiguration[]
  total: number
  page: number
  page_size: number
}

export interface InspectionRun {
  id?: string
  error?: string
  run_date: string
  status: string
  started_at: string | null
  finished_at: string | null
  total: number
  completed: number
  failed: number
  skipped: number
  interrupted: number
  saved: number
}

export interface InspectionItem {
  id: string
  shop_code: string
  channel: number
  camera_name: string
  region: string
  status: string
  coverage_method: string
  message: string
  captured_at: string | null
  has_evidence: boolean
  has_record: boolean
  excluded?: boolean
  can_exclude?: boolean
}

export interface InspectionItemPage {
  items: InspectionItem[]
  total: number
  page: number
  page_size: number
}

export interface SnapshotBatchReport {
  id: string
  status: string
  started_at: string
  finished_at: string | null
  error?: string
  items: InspectionItem[]
}

export const inspectionStatusKeys: Record<string, MessageKey> = {
  running: 'runRunning', completed: 'runCompleted', interrupted: 'runInterrupted', missed: 'runMissed',
  pending: 'runPending', captured: 'runCaptured', failed: 'runFailed', skipped: 'runSkipped',
  capturing: 'runCapturing',
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

function isCameraConfigurationPage(value: unknown): value is CameraConfigurationPage {
  return isObject(value) && ['total', 'page', 'page_size'].every(key => typeof value[key] === 'number') &&
    Array.isArray(value.rows) && value.rows.every(row =>
      isObject(row) && ['shop_code', 'shop_name', 'company', 'region', 'brand', 'internal_ip']
        .every(key => row[key] === null || typeof row[key] === 'string') &&
      ['port', 'cam_qty'].every(key => row[key] === null || typeof row[key] === 'number') &&
      typeof row.credentials_complete === 'boolean',
    )
}

function isInspectionRun(value: unknown): value is InspectionRun {
  return isObject(value) && typeof value.run_date === 'string' && typeof value.status === 'string' &&
    (value.error === undefined || typeof value.error === 'string') &&
    ['started_at', 'finished_at'].every(key => value[key] === null || typeof value[key] === 'string') &&
    ['total', 'completed', 'failed', 'skipped', 'interrupted', 'saved'].every(key => typeof value[key] === 'number')
}

function isSnapshotBatch(value: unknown): value is InspectionRun & { id: string } {
  return isInspectionRun(value) && typeof value.id === 'string'
}

function isInspectionItem(value: unknown): value is InspectionItem {
  return isObject(value) && ['id', 'shop_code', 'camera_name', 'region', 'status', 'coverage_method', 'message']
    .every(key => typeof value[key] === 'string') &&
    typeof value.channel === 'number' && (value.captured_at === null || typeof value.captured_at === 'string') &&
    typeof value.has_evidence === 'boolean' && typeof value.has_record === 'boolean' &&
    ['excluded', 'can_exclude'].every(key => value[key] === undefined || typeof value[key] === 'boolean')
}

function isInspectionItemPage(value: unknown): value is InspectionItemPage {
  return isObject(value) && ['total', 'page', 'page_size'].every(key => typeof value[key] === 'number') &&
    Array.isArray(value.items) && value.items.every(isInspectionItem)
}

function isSnapshotBatchReport(value: unknown): value is SnapshotBatchReport {
  return isObject(value) && ['id', 'status', 'started_at'].every(key => typeof value[key] === 'string') &&
    (value.finished_at === null || typeof value.finished_at === 'string') &&
    (value.error === undefined || typeof value.error === 'string') &&
    Array.isArray(value.items) && value.items.every(isInspectionItem)
}

async function request<T>(
  url: string, validate: (value: unknown) => value is T, options?: RequestInit,
): Promise<T> {
  const response = await fetch(url, options)
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('cctv-session-expired'))
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
    if (message === 'Manual snapshot capture is not configured.') code = 'snapshotConfigurationError'
    if (message === 'A manual snapshot batch is already running.') code = 'batchAlreadyRunning'
    if (message === 'No DAHAU recorders are configured for batch capture.') code = 'batchNoRecorders'
    if (message === 'A unique current recorder configuration is required to exclude this channel.') code = 'batchExclusionAmbiguous'
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

export type CameraBrandFilter = '' | 'DAHAU' | 'HIKVISION'

export function fetchCameraConfiguration(page: number, signal: AbortSignal, brand: CameraBrandFilter) {
  const params = new URLSearchParams({ page: String(page), page_size: '20' })
  if (brand) params.set('brand', brand)
  return request(`/api/camera-configuration?${params}`, isCameraConfigurationPage, { signal, cache: 'no-store' })
}

export interface SnapshotRecorder {
  shop: string
  region: string
  name: string | null
  channels: number
}

export function fetchSnapshotRecorders(signal: AbortSignal) {
  return request('/api/camera-configuration/snapshot-recorders', (value): value is SnapshotRecorder[] =>
    Array.isArray(value) && value.every(row => isObject(row) && typeof row.shop === 'string' &&
      typeof row.region === 'string' && (row.name === null || typeof row.name === 'string') &&
      typeof row.channels === 'number' && Number.isInteger(row.channels) && row.channels >= 1 && row.channels <= 256),
    { signal, cache: 'no-store' })
}

export async function captureCameraSnapshot(shop_code: string, region: string, channel: number) {
  const response = await fetch('/api/camera-configuration/snapshot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: JSON.stringify({ shop_code, region, channel }),
  })
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event('cctv-session-expired'))
    if (response.headers.get('content-type')?.includes('application/json')) {
      const body: unknown = await response.json()
      if (isObject(body) && body.detail === 'Manual snapshot capture is not configured.') {
        throw new ApiError('snapshotConfigurationError', body.detail)
      }
    }
    throw new Error(`Manual snapshot failed (HTTP ${response.status}).`)
  }
  const image = await response.blob()
  if (image.type !== 'image/jpeg' || !image.size) throw new Error('Empty or invalid snapshot response.')
  return image
}

export function startSnapshotBatch(recheckSkipped = false) {
  return request(`/api/snapshot-batches?recheck_skipped=${recheckSkipped}`, isSnapshotBatch, {
    method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' },
  })
}

export function fetchSnapshotBatchReport(id: string, signal: AbortSignal) {
  return request(`/api/snapshot-batches/${encodeURIComponent(id)}/report`,
    (value): value is SnapshotBatchReport => isSnapshotBatchReport(value) && value.id === id,
    { signal, cache: 'no-store' })
}

export function setSnapshotChannelExcluded(batch: string, item: string, skip: boolean) {
  return request(`/api/snapshot-batches/${encodeURIComponent(batch)}/items/${encodeURIComponent(item)}/exclusion`,
    (value): value is { skip: boolean } => isObject(value) && typeof value.skip === 'boolean', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ skip }),
    })
}

export function fetchInspectionRuns(signal: AbortSignal, manual = false) {
  return request(manual ? '/api/snapshot-batches' : '/api/inspection-runs', (value): value is InspectionRun[] =>
    Array.isArray(value) && value.every(manual ? isSnapshotBatch : isInspectionRun), { signal, cache: 'no-store' })
}

export function fetchInspectionItems(id: string, page: number, signal: AbortSignal, manual = false) {
  const params = new URLSearchParams({ page: String(page), page_size: '20' })
  const base = manual ? '/api/snapshot-batches' : '/api/inspection-runs'
  return request(`${base}/${encodeURIComponent(id)}/items?${params}`, isInspectionItemPage,
    { signal, cache: 'no-store' })
}

export function fetchRecord(id: string) {
  return request(`/api/recognitions/${encodeURIComponent(id)}`, isRecord)
}

export function uploadScreenshot(body: FormData) {
  return request('/api/recognitions', isRecord, { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' }, body })
}

export function saveReview(id: string, status: ReviewStatus, note: string) {
  return request(`/api/recognitions/${id}/review`, isRecord, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
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
  return request(`/api/recognitions/${id}/baseline`, isBaseline, { method: 'POST', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
}

export async function authRequest(path: 'login' | 'session' | 'logout', body?: { username: string; password: string; remember_me?: boolean }, signal?: AbortSignal) {
  const response = await fetch(`/api/auth/${path}`, {
    method: path === 'session' ? 'GET' : 'POST',
    headers: path === 'session' ? undefined : { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
    body: body ? JSON.stringify(body) : undefined,
    signal,
  })
  if (!response.ok) throw new AuthError(response.status)
  const value: unknown = await response.json()
  if (!isObject(value) || (path === 'logout' ? value.status !== 'ok' : typeof value.username !== 'string')) {
    throw new AuthError(0)
  }
}

export class AuthError extends Error {
  readonly status: number
  constructor(status: number) {
    super(`Authentication request failed (HTTP ${status}).`)
    this.status = status
  }
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
