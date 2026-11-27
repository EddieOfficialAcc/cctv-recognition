import { useEffect, useState } from 'react'
import { ApiError, comparisonEvidenceUrl, fetchComparison, type Comparison } from '../api'
import { useLocale } from '../localization'
import { EvidenceImage } from './EvidenceImage'

export type ComparisonState =
  | { status: 'loading' }
  | { status: 'unavailable' }
  | { status: 'error' }
  | { status: 'available'; comparison: Comparison }

export function DailyComparisonView({ recordId, state }: { recordId: string; state: ComparisonState }) {
  const { t, formatDate } = useLocale()
  return <section className="review-form">
    <div className="section-heading"><h3>{t('comparisonTitle')}</h3></div>
    {state.status === 'loading' && <p className="field-hint" role="status">{t('comparisonLoading')}</p>}
    {state.status === 'unavailable' && <p className="field-hint" role="status">{t('comparisonUnavailable')}</p>}
    {state.status === 'error' && <p className="error-message" role="alert">{t('comparisonError')}</p>}
    {state.status === 'available' && <>
      <p className="field-hint">{t('comparisonCapturedAt', { date: formatDate(state.comparison.captured_at, true) })}</p>
      <EvidenceImage src={comparisonEvidenceUrl(recordId)} alt={t('comparisonEvidenceAlt')} errorText={t('comparisonEvidenceError')} />
    </>}
  </section>
}

export function DailyComparison({ recordId }: { recordId: string }) {
  const [result, setResult] = useState<{ recordId: string; state: ComparisonState } | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void fetchComparison(recordId, controller.signal).then(comparison => {
      if (!controller.signal.aborted) setResult({ recordId, state: { status: 'available', comparison } })
    }).catch(cause => {
      if (controller.signal.aborted) return
      if (cause instanceof ApiError && cause.code === 'notFoundError') {
        setResult({ recordId, state: { status: 'unavailable' } })
      } else {
        console.error('Previous-day comparison could not be loaded:', cause)
        setResult({ recordId, state: { status: 'error' } })
      }
    })
    return () => controller.abort()
  }, [recordId])
  return <DailyComparisonView recordId={recordId} state={result?.recordId === recordId ? result.state : { status: 'loading' }} />
}
