import { useEffect, useState, type FormEvent } from 'react'
import {
  ApiError, approveBaseline, categoryTitleKeys, checkKeys, fetchBaseline, fetchBurstInfo, outcomeKeys, regionKeys, reviewKeys, saveReview,
  type Baseline, type RecognitionRecord, type ReviewStatus,
} from '../api'
import { Dialog } from './Dialog'
import { DailyComparison } from './DailyComparison'
import { EvidenceImage } from './EvidenceImage'
import { Icon } from './Icon'
import { useLocale } from '../localization'
import { parseTimestampDetails, type TimestampDetails } from '../timestamp'
import type { MessageKey } from '../messages'

const checkIcons = { signal: 'camera', timestamp: 'clock', scene: 'image' } as const
const timestampStates: Record<TimestampDetails['state'], MessageKey> = {
  readable: 'timestampStateReadable', missing: 'timestampStateMissing',
  unreadable: 'timestampStateUnreadable', ambiguous: 'timestampStateAmbiguous',
}
const timestampReasons: Record<TimestampDetails['reason'], MessageKey> = {
  missing: 'timestampReasonMissing', unreadable: 'timestampReasonUnreadable',
  ambiguous: 'timestampReasonAmbiguous', invalid: 'timestampReasonInvalid',
  capture_missing: 'timestampReasonCaptureMissing', boundary: 'timestampReasonBoundary',
  outside: 'timestampReasonOutside', within: 'timestampReasonWithin',
  comparison_disagreement: 'timestampReasonComparisonDisagreement',
}
const timestampComparisons = {
  within: 'timestampModelWithin', outside: 'timestampModelOutside', not_assessed: 'timestampModelNotAssessed',
} as const

export function RecordDrawer({ record, onClose, onSaved }: {
  record: RecognitionRecord; onClose: () => void; onSaved: (record: RecognitionRecord) => void
}) {
  const { t, formatDate, errorMessage, checkExplanation } = useLocale()
  const [status, setStatus] = useState<ReviewStatus>(record.review_status)
  const [note, setNote] = useState(record.review_note)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const [burst, setBurst] = useState<{ frame_count: number; duration_seconds: number } | null>(null)
  const [burstError, setBurstError] = useState<Error | null>(null)
  const [frameIndex, setFrameIndex] = useState(0)
  const [baseline, setBaseline] = useState<Baseline | null>(null)
  const [baselineLoaded, setBaselineLoaded] = useState(false)
  const [baselineBusy, setBaselineBusy] = useState(false)
  const [baselineError, setBaselineError] = useState<Error | null>(null)
  const [confirmBaseline, setConfirmBaseline] = useState(false)
  const hasCameraMetadata = Boolean(record.shop_code.trim() && record.camera_name.trim())
  const isDaily = record.analyzer_version === 'vision-llm-daily-v1'

  useEffect(() => {
    if (!['burst-statistics-v1', 'vision-llm-burst-v1', 'vision-llm-burst-v2'].includes(record.analyzer_version)) return
    const controller = new AbortController()
    fetchBurstInfo(record.id, controller.signal).then(setBurst).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Burst evidence could not be loaded:', cause)
      setBurstError(cause instanceof Error ? cause : new Error(String(cause)))
    })
    return () => controller.abort()
  }, [record.id, record.analyzer_version])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || baselineBusy) return
    setBusy(true); setError(null); setSaved(false)
    try {
      onSaved(await saveReview(record.id, status, note))
      setSaved(true)
    } catch (cause) {
      console.error('Review save failed:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally { setBusy(false) }
  }

  async function loadBaseline() {
    setBaselineBusy(true); setBaselineError(null)
    try {
      setBaseline(await fetchBaseline(record.id))
      setBaselineLoaded(true)
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'notFoundError') {
        setBaseline(null); setBaselineLoaded(true)
      } else {
        console.error('Baseline could not be loaded:', cause)
        setBaselineError(cause instanceof Error ? cause : new Error(String(cause)))
      }
    } finally { setBaselineBusy(false) }
  }

  async function setCurrentBaseline() {
    setBaselineBusy(true); setBaselineError(null)
    try {
      setBaseline(await approveBaseline(record.id))
      setBaselineLoaded(true)
      setConfirmBaseline(false)
    } catch (cause) {
      console.error('Baseline approval failed:', cause)
      setBaselineError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally { setBaselineBusy(false) }
  }

  return <Dialog title={record.camera_name || t('notProvided')} subtitle={`${record.shop_code || t('notProvided')} / ${t(regionKeys[record.region])}`}
    drawer onClose={onClose} busy={busy || baselineBusy}>
    <div className="dialog-body drawer-body">
      <div className="record-heading">
        <span className={`badge ${record.outcome}`}>{t(outcomeKeys[record.outcome])}</span>
        <span className="muted small">{t(reviewKeys[record.review_status])}</span>
      </div>
      <p id="dialog-description" className="muted">{t(isDaily ? 'dailyReviewDescription' : 'reviewDescription')}</p>
      {isDaily && <div className="section-heading"><h3>{t('currentCaptureTitle')}</h3></div>}
      <EvidenceImage key={`${record.id}-${frameIndex}`} id={record.id} frameIndex={frameIndex}
        alt={t(isDaily ? 'currentCaptureAlt' : 'evidenceAlt', { camera: record.camera_name || t('notProvided') })} />
      <div className="evidence-caption"><span title={record.filename}>{record.filename}</span><span>{record.width} × {record.height}</span></div>
      {burst && <div>
        <p className="field-hint">{t('burstReviewHint', { count: burst.frame_count, seconds: burst.duration_seconds })}</p>
        <label>{t('burstFrame')}
          <select value={frameIndex} onChange={event => setFrameIndex(Number(event.target.value))}>
            {Array.from({ length: burst.frame_count }, (_, index) =>
              <option key={index} value={index}>{t('burstFrameNumber', { number: index + 1 })}</option>)}
          </select>
        </label>
      </div>}
      {burstError && <div className="error-message" role="alert">{errorMessage(burstError)}</div>}
      <dl className="metadata-grid">
        <div><dt>{t('uploadedTime')}</dt><dd>{formatDate(record.uploaded_at)}</dd></div>
        <div><dt>{t('capturedTime')}</dt><dd>{formatDate(record.captured_at)}</dd></div>
      </dl>
      {isDaily && <DailyComparison key={record.id} recordId={record.id} />}
      <div className="section-heading"><h3>{t('recognitionResults')}</h3><span className="muted small">{t('coreChecks')}</span></div>
      <div className="check-list">{record.checks.map(check => {
        const isTimestampV2 = check.category === 'timestamp' &&
          ['vision-llm-v2', 'vision-llm-burst-v2', 'vision-llm-daily-v1'].includes(record.analyzer_version)
        const details = isTimestampV2 ? parseTimestampDetails(check.explanation) : null
        return <section className="check-card" key={check.category}>
          <div className="check-card-heading"><Icon name={checkIcons[check.category]} size={18} />
            <h4>{t(categoryTitleKeys[check.category])}</h4>
            <span className={`badge check-${check.status}`}>{t(checkKeys[check.status])}</span>
          </div>
          {isTimestampV2 ? details ? <>
            <p>{t(timestampReasons[details.reason])}</p>
            <dl className="timestamp-details">
              <div><dt>{t('timestampOverlay')}</dt><dd>{t(timestampStates[details.state])}</dd></div>
              <div><dt>{t('timestampReading')}</dt><dd>{details.reading ?? t('notProvided')}</dd></div>
              <div><dt>{t('timestampReference')}</dt><dd>{formatDate(record.captured_at, true)}</dd></div>
              <div><dt>{t('timestampDifference')}</dt><dd>{details.difference_seconds === null
                ? t('notProvided')
                : t('timestampDifferenceValue', { seconds: `${details.difference_seconds >= 0 ? '+' : ''}${details.difference_seconds}` })}</dd></div>
              <div><dt>{t('timestampThreshold')}</dt><dd>{t('timestampSeconds', { seconds: details.threshold_seconds })}</dd></div>
              {details.model_comparison && <div><dt>{t('timestampModelComparison')}</dt>
                <dd>{t(timestampComparisons[details.model_comparison])}</dd></div>}
            </dl>
          </> : <p>{t('timestampDetailsUnavailable')}</p> : <p>{checkExplanation(record, check)}</p>}
        </section>
      })}</div>
      <p className="analyzer-label">{t('analyzerDisclaimer', { version: record.analyzer_version })}</p>
      <div className="review-form">
        <div className="section-heading"><h3>{t('baselineTitle')}</h3></div>
        <p className="field-hint">{t(isDaily ? 'dailyBaselineHint' : 'baselineHint')}</p>
        {!hasCameraMetadata && <p className="field-hint">{t('baselineMetadataRequired')}</p>}
        <div className="baseline-actions">
          <button type="button" className="button secondary" disabled={!hasCameraMetadata || busy || baselineBusy} onClick={loadBaseline}>
            {t('baselineLoad')}
          </button>
          {!confirmBaseline && <button type="button" className="button secondary" disabled={!hasCameraMetadata || busy || baselineBusy}
            onClick={() => setConfirmBaseline(true)}>{t('baselineApprove')}</button>}
        </div>
        {baselineLoaded && !baseline && <p className="field-hint">{t('baselineNone')}</p>}
        {baseline && <>
          <p className="field-hint">{t('baselineApprovedAt', { date: formatDate(baseline.approved_at) })}</p>
          <EvidenceImage key={baseline.record_id} id={baseline.record_id} alt={t('baselineEvidenceAlt')} />
        </>}
        {baselineError && <div className="error-message" role="alert">{errorMessage(baselineError)}</div>}
        {confirmBaseline &&
          <div>
            <p className="field-hint">{t('baselineConfirmHint')}</p>
            <div className="baseline-actions">
              <button type="button" className="button primary" disabled={busy || baselineBusy}
                onClick={setCurrentBaseline}>{t('baselineConfirm')}</button>
              <button type="button" className="button secondary" disabled={baselineBusy}
                onClick={() => setConfirmBaseline(false)}>{t('cancel')}</button>
            </div>
          </div>}
      </div>
      <form className="review-form" onSubmit={submit}>
        <div className="section-heading"><h3>{t('operatorReview')}</h3><Icon name="shield" size={18} /></div>
        <fieldset disabled={busy || baselineBusy}>
          <label>{t('reviewStatus')}
            <select value={status} onChange={event => {
              const value = event.target.value
              if (value === 'new' || value === 'acknowledged' || value === 'false_positive') {
                setStatus(value); setSaved(false)
              }
            }}>
              <option value="new">{t('unreviewed')}</option><option value="acknowledged">{t('acknowledged')}</option>
              <option value="false_positive">{t('falsePositive')}</option>
            </select>
          </label>
          <label>{t('reviewNote')} <span className="optional">{t('optional')}</span>
            <textarea value={note} maxLength={1000} rows={3} placeholder={t('reviewPlaceholder')}
              onChange={event => { setNote(event.target.value); setSaved(false) }} />
          </label>
        </fieldset>
        <p className="field-hint">{t('reviewHint')}</p>
        {record.reviewed_at && <p className="field-hint">{t('lastReviewed', { date: formatDate(record.reviewed_at) })}</p>}
        {error && <div className="error-message" role="alert">{errorMessage(error)}</div>}
        {saved && <p className="success-message" role="status"><Icon name="check" size={16} /> {t('reviewSaved')}</p>}
        <button className="button primary" type="submit" disabled={busy || baselineBusy}>
          {busy ? <span className="spinner" /> : <Icon name="check" size={17} />}
          {t(busy ? 'savingReview' : 'saveReview')}
        </button>
      </form>
      <div className="record-id">{t('recordId')} <span>{record.id}</span></div>
    </div>
  </Dialog>
}
