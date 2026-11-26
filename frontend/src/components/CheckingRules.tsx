import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  ApiError, fetchCheckingRules, saveCheckingRules, timestampPositions,
  type RulesPage, type RuleValues, type TimestampPosition,
} from '../api'
import { useLocale } from '../localization'
import { Icon } from './Icon'

const positionKeys = {
  'upper-right': 'rulesUpperRight', 'upper-left': 'rulesUpperLeft',
  'lower-right': 'rulesLowerRight', 'lower-left': 'rulesLowerLeft',
} as const

function valuesOf(values: RuleValues): RuleValues {
  return {
    signal_instructions: values.signal_instructions, scene_instructions: values.scene_instructions,
    timestamp_position: values.timestamp_position, timestamp_drift_seconds: values.timestamp_drift_seconds,
  }
}

export function CheckingRules() {
  const { t, formatDate, errorMessage } = useLocale()
  const [data, setData] = useState<RulesPage | null>(null)
  const [draft, setDraft] = useState<RuleValues | null>(null)
  const [drift, setDrift] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const active = useRef(true)
  const firstField = useRef<HTMLTextAreaElement>(null)
  const editing = draft !== null
  const values = draft ?? data?.rules
  const dirty = Boolean(draft && data && (
    draft.signal_instructions !== data.rules.signal_instructions ||
    draft.scene_instructions !== data.rules.scene_instructions ||
    draft.timestamp_position !== data.rules.timestamp_position ||
    Number(drift) !== data.rules.timestamp_drift_seconds
  ))

  useEffect(() => {
    active.current = true
    return () => { active.current = false }
  }, [])

  useEffect(() => {
    if (editing) firstField.current?.focus()
  }, [editing])

  useEffect(() => {
    const controller = new AbortController()
    void fetchCheckingRules(controller.signal).then(result => {
      if (!controller.signal.aborted) { setData(result); setError(null); setLoading(false) }
    }).catch(cause => {
      if (!controller.signal.aborted) {
        console.error('Checking rules failed to load:', cause)
        setError(cause instanceof Error ? cause : new Error(String(cause))); setLoading(false)
      }
    })
    return () => controller.abort()
  }, [refresh])

  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  function reload() {
    setDraft(null); setData(null); setLoading(true); setError(null); setSaved(false)
    setRefresh(value => value + 1)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!draft || !data || busy) return
    const seconds = Number(drift)
    if (!draft.signal_instructions.trim() || !draft.scene_instructions.trim() ||
      !Number.isInteger(seconds) || seconds < 1 || seconds > 86400) {
      setError(new ApiError('rulesValidationError', 'Invalid checking rules.'))
      return
    }
    setBusy(true); setError(null); setSaved(false)
    try {
      const result = await saveCheckingRules({ ...draft, timestamp_drift_seconds: seconds }, data.rules.revision)
      if (active.current) { setData(result); setDraft(null); setSaved(true) }
    } catch (cause) {
      console.error('Checking rules could not be saved:', cause)
      if (active.current) setError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally {
      if (active.current) setBusy(false)
    }
  }

  return <>
    <div className="page-heading"><div><p className="eyebrow">{t('monitoringEyebrow')}</p>
      <h1>{t('checkingRules')}</h1><p className="page-description">{t('rulesDescription')}</p></div>
      <div className="configuration-actions">
        {!editing && data && !loading && <button className="button primary" onClick={() => {
          setDraft(valuesOf(data.rules)); setDrift(String(data.rules.timestamp_drift_seconds)); setSaved(false); setError(null)
        }}><Icon name="sliders" size={16} />{t('rulesEdit')}</button>}
        <button className="button secondary" onClick={reload} disabled={loading || editing || busy}>
          <Icon name="refresh" size={16} />{t('refresh')}</button>
      </div>
    </div>
    <div className="coverage-banner"><Icon name="info" size={21} />
      <div><strong>{t('rulesSharedTitle')}</strong><p>{t('rulesScope')}</p></div></div>
    {error && <div className="error-message rules-notice" role="alert">{errorMessage(error)}
      {error instanceof ApiError && error.code === 'rulesConflictError'
        ? <button className="text-button" disabled={busy} onClick={reload}>{t('rulesReload')}</button>
        : !data && <button className="text-button" onClick={reload}>{t('tryAgain')}</button>}
    </div>}
    {saved && <p className="notice success-notice" role="status"><Icon name="check" size={18} />{t('rulesSaved')}</p>}
    {loading ? <div className="empty-state" role="status"><span className="spinner large" /><p>{t('rulesLoading')}</p></div> :
      values && data && <form className="rules-form" onSubmit={event => { void submit(event) }}>
        <div className="rules-version"><span>{data.rules.updated_at
          ? t('rulesUpdated', { revision: data.rules.revision, time: formatDate(data.rules.updated_at) })
          : t('rulesUsingDefaults')}</span>{editing && <strong role="status">{t(dirty ? 'rulesUnsaved' : 'rulesEditing')}</strong>}</div>
        <fieldset disabled={busy}>
          <section className="records-panel rules-card" aria-labelledby="signal-rules">
            <div className="panel-heading"><div className="section-heading"><span className="stat-icon teal"><Icon name="image" size={20} /></span>
              <h2 id="signal-rules">{t('signalTitle')}</h2></div><span className="count-badge">01</span></div>
            <div className="rules-card-body"><p>{t('rulesSignalHint')}</p>
              {draft ? <label>{t('rulesSignalCriteria')}<textarea ref={firstField} required maxLength={1500} rows={5}
                value={draft.signal_instructions} onChange={event => setDraft({ ...draft, signal_instructions: event.target.value })} /></label>
                : <div className="rule-criteria">{values.signal_instructions}</div>}
            </div>
          </section>
          <section className="records-panel rules-card" aria-labelledby="timestamp-rules">
            <div className="panel-heading"><div className="section-heading"><span className="stat-icon blue"><Icon name="clock" size={20} /></span>
              <h2 id="timestamp-rules">{t('timestampTitle')}</h2></div><span className="count-badge">02</span></div>
            <div className="rules-card-body"><p>{t('rulesTimestampHint')}</p>
              {draft ? <div className="rules-timestamp-fields">
                <label>{t('rulesTimestampPosition')}<select value={draft.timestamp_position} onChange={event => {
                  const position: TimestampPosition | undefined = timestampPositions.find(value => value === event.target.value)
                  if (position) setDraft({ ...draft, timestamp_position: position })
                }}>{timestampPositions.map(position => <option value={position} key={position}>{t(positionKeys[position])}</option>)}</select></label>
                <label>{t('rulesTimestampDrift')}<input type="number" required min={1} max={86400} step={1} value={drift}
                  onChange={event => setDrift(event.target.value)} /><span className="field-hint">{t('rulesDriftRange')}</span></label>
              </div> : <dl className="rules-timestamp-fields"><div><dt>{t('rulesTimestampPosition')}</dt><dd>{t(positionKeys[values.timestamp_position])}</dd></div>
                <div><dt>{t('rulesTimestampDrift')}</dt><dd>{t('rulesSeconds', { count: values.timestamp_drift_seconds })}</dd></div></dl>}
              <p className="field-hint">{t('rulesTimestampReference')}</p>
              <p className="field-hint">{t('rulesTimestampSafety')}</p>
            </div>
          </section>
          <section className="records-panel rules-card" aria-labelledby="scene-rules">
            <div className="panel-heading"><div className="section-heading"><span className="stat-icon purple"><Icon name="camera" size={20} /></span>
              <h2 id="scene-rules">{t('sceneTitle')}</h2></div><span className="count-badge">03</span></div>
            <div className="rules-card-body"><p>{t('rulesSceneHint')}</p>
              {draft ? <label>{t('rulesSceneCriteria')}<textarea required maxLength={1500} rows={5} value={draft.scene_instructions}
                onChange={event => setDraft({ ...draft, scene_instructions: event.target.value })} /></label>
                : <div className="rule-criteria">{values.scene_instructions}</div>}
              <p className="field-hint">{t('rulesBaselineSafety')}</p>
            </div>
          </section>
          {draft && <div className="rules-save-bar">
            <p>{t('rulesInstructionsHint')}</p><div className="configuration-actions">
              <button type="button" className="button secondary" onClick={() => {
                setDraft(valuesOf(data.defaults)); setDrift(String(data.defaults.timestamp_drift_seconds)); setError(null)
              }}>{t('rulesReset')}</button>
              <button type="button" className="button secondary" onClick={() => { setDraft(null); setError(null) }}>{t('cancel')}</button>
              <button type="submit" className="button primary" disabled={!dirty || busy}>
                {busy ? <span className="spinner" /> : <Icon name="check" size={16} />}{t(busy ? 'rulesSaving' : 'rulesSave')}</button>
            </div>
          </div>}
        </fieldset>
      </form>}
  </>
}
