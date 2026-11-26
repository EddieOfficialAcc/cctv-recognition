export type TimestampDetails = {
  state: 'readable' | 'missing' | 'unreadable' | 'ambiguous'
  reading: string | null
  difference_seconds: number | null
  threshold_seconds: number
  reason: 'missing' | 'unreadable' | 'ambiguous' | 'invalid' | 'capture_missing' | 'boundary' | 'outside' | 'within' | 'comparison_disagreement'
  model_comparison?: 'within' | 'outside' | 'not_assessed'
}

export function parseTimestampDetails(explanation: string): TimestampDetails | null {
  let value: unknown
  try {
    value = JSON.parse(explanation)
  } catch {
    return null
  }
  if (typeof value !== 'object' || value === null ||
      !('state' in value) || !('reading' in value) || !('difference_seconds' in value) ||
      !('threshold_seconds' in value) || !('reason' in value)) return null
  const { state, reading, difference_seconds: difference, threshold_seconds: threshold, reason } = value
  if (state !== 'readable' && state !== 'missing' && state !== 'unreadable' && state !== 'ambiguous') return null
  if (!(reading === null || typeof reading === 'string') ||
      !(difference === null || (typeof difference === 'number' && Number.isSafeInteger(difference))) ||
      typeof threshold !== 'number' || !Number.isSafeInteger(threshold) || threshold < 1 ||
      (reason !== 'missing' && reason !== 'unreadable' && reason !== 'ambiguous' && reason !== 'invalid' &&
       reason !== 'capture_missing' && reason !== 'boundary' && reason !== 'outside' && reason !== 'within' &&
       reason !== 'comparison_disagreement')) return null
  const modelComparison = 'model_comparison' in value ? value.model_comparison : undefined
  if (modelComparison !== undefined && modelComparison !== 'within' && modelComparison !== 'outside' &&
    modelComparison !== 'not_assessed') return null
  return {
    state, reading, difference_seconds: difference, threshold_seconds: threshold, reason,
    ...(modelComparison === undefined ? {} : { model_comparison: modelComparison }),
  }
}
