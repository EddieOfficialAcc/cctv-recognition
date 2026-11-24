import { useState, type FormEvent } from 'react'
import {
  categoryTitleKeys, checkKeys, outcomeKeys, regionKeys, reviewKeys, saveReview,
  type RecognitionRecord, type ReviewStatus,
} from '../api'
import { Dialog } from './Dialog'
import { EvidenceImage } from './EvidenceImage'
import { Icon } from './Icon'
import { useLocale } from '../localization'

const checkIcons = { signal: 'camera', timestamp: 'clock', scene: 'image' } as const

export function RecordDrawer({ record, onClose, onSaved }: {
  record: RecognitionRecord; onClose: () => void; onSaved: (record: RecognitionRecord) => void
}) {
  const { t, formatDate, errorMessage, checkExplanation } = useLocale()
  const [status, setStatus] = useState<ReviewStatus>(record.review_status)
  const [note, setNote] = useState(record.review_note)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true); setError(null); setSaved(false)
    try {
      onSaved(await saveReview(record.id, status, note))
      setSaved(true)
    } catch (cause) {
      console.error('Review save failed:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally { setBusy(false) }
  }

  return <Dialog title={record.camera_name} subtitle={`${record.shop_code} / ${t(regionKeys[record.region])}`}
    drawer onClose={onClose} busy={busy}>
    <div className="dialog-body drawer-body">
      <div className="record-heading">
        <span className={`badge ${record.outcome}`}>{t(outcomeKeys[record.outcome])}</span>
        <span className="muted small">{t(reviewKeys[record.review_status])}</span>
      </div>
      <p id="dialog-description" className="muted">{t('reviewDescription')}</p>
      <EvidenceImage key={record.id} id={record.id} alt={t('evidenceAlt', { camera: record.camera_name })} />
      <div className="evidence-caption"><span title={record.filename}>{record.filename}</span><span>{record.width} × {record.height}</span></div>
      <dl className="metadata-grid">
        <div><dt>{t('uploadedTime')}</dt><dd>{formatDate(record.uploaded_at)}</dd></div>
        <div><dt>{t('capturedTime')}</dt><dd>{formatDate(record.captured_at)}</dd></div>
      </dl>
      <div className="section-heading"><h3>{t('recognitionResults')}</h3><span className="muted small">{t('coreChecks')}</span></div>
      <div className="check-list">{record.checks.map(check =>
        <section className="check-card" key={check.category}>
          <div className="check-card-heading"><Icon name={checkIcons[check.category]} size={18} />
            <h4>{t(categoryTitleKeys[check.category])}</h4>
            <span className={`badge check-${check.status}`}>{t(checkKeys[check.status])}</span>
          </div>
          <p>{checkExplanation(record, check)}</p>
        </section>,
      )}</div>
      <p className="analyzer-label">{t('analyzerDisclaimer', { version: record.analyzer_version })}</p>
      <form className="review-form" onSubmit={submit}>
        <div className="section-heading"><h3>{t('operatorReview')}</h3><Icon name="shield" size={18} /></div>
        <fieldset disabled={busy}>
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
        <button className="button primary" type="submit" disabled={busy}>
          {busy ? <span className="spinner" /> : <Icon name="check" size={17} />}
          {t(busy ? 'savingReview' : 'saveReview')}
        </button>
      </form>
      <div className="record-id">{t('recordId')} <span>{record.id}</span></div>
    </div>
  </Dialog>
}
