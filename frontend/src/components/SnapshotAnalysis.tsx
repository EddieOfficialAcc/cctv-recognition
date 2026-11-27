import { useEffect, useState } from 'react'
import {
  analysisSource, analysisStatusKeys, fetchAnalysis, fetchAnalysisSettings, fetchRecord, outcomeKeys,
  retryAnalysis, setAnalysisStopped, setAutoAnalysis, startBatchAnalysis,
  type AnalysisPage, type AnalysisSettings, type AnalysisStatus, type InspectionRun, type RecognitionRecord,
} from '../api'
import { useLocale } from '../localization'
import { Dialog } from './Dialog'
import { Icon } from './Icon'

export function AnalysisProgress({ summary, saved }: { summary: AnalysisPage['summary']; saved: number }) {
  const { t } = useLocale()
  return <div className="analysis-progress">
    <label>{t('aiProgress', { done: summary.completed + summary.failed, total: summary.total, saved })}
      <progress max={Math.max(1, summary.total)} value={summary.completed + summary.failed} aria-label={t('aiProgressLabel')} />
    </label>
    <p className="sr-only" role="status">{t('aiCounts', {
      queued: summary.queued, analyzing: summary.analyzing, completed: summary.completed,
      failed: summary.failed, stopped: summary.stopped, flagged: summary.flagged,
    })}</p>
    <dl className="analysis-metrics">
      {([
        ['queued', 'aiQueued'], ['analyzing', 'aiAnalyzing'], ['completed', 'runCompleted'],
        ['failed', 'runFailed'], ['stopped', 'runStopped'], ['flagged', 'flagged'],
      ] as const).map(([key, title]) => <div key={key} className={`analysis-metric analysis-metric-${key}`}>
        <dt>{t(title)}</dt><dd>{summary[key]}</dd>
      </div>)}
    </dl>
    {!summary.worker_online && (summary.queued > 0 || summary.analyzing > 0) &&
      <p className="error-message" role="alert">{t('aiOffline')}</p>}
    {summary.stop_requested && <p role="status">{t(summary.analyzing ? 'aiStoppingHint' : 'aiStoppedHint')}</p>}
  </div>
}

export function SnapshotAnalysis({ run, manual, onSelect }: {
  run?: InspectionRun; manual: boolean; onSelect: (record: RecognitionRecord) => void
}) {
  const { t, formatDate, errorMessage } = useLocale()
  const id = run ? run.id ?? run.run_date : ''
  const [settings, setSettings] = useState<AnalysisSettings | null>(null)
  const [data, setData] = useState<AnalysisPage | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [actionError, setActionError] = useState<Error | null>(null)
  const [busy, setBusy] = useState(false)
  const [autoSaved, setAutoSaved] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [confirm, setConfirm] = useState(false)
  const [limit, setLimit] = useState<20 | null>(20)
  const [view, setView] = useState(false)
  const [status, setStatus] = useState<AnalysisStatus | ''>('')
  const [page, setPage] = useState(1)
  const captureRunning = run?.status === 'running'

  useEffect(() => {
    const controller = new AbortController()
    void Promise.all([
      fetchAnalysisSettings(controller.signal),
      id ? fetchAnalysis(id, manual, page, status, controller.signal) : Promise.resolve(null),
    ]).then(([nextSettings, result]) => {
      if (controller.signal.aborted) return
      if (result && page > Math.max(1, Math.ceil(result.total / result.page_size))) {
        setPage(Math.max(1, Math.ceil(result.total / result.page_size)))
        return
      }
      setSettings(nextSettings); setData(result); setError(null)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Snapshot AI review failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    })
    return () => controller.abort()
  }, [id, manual, page, status, refresh])

  useEffect(() => {
    if (error || (!captureRunning && !data?.summary.queued && !data?.summary.analyzing)) return
    const timer = setInterval(() => setRefresh(value => value + 1), 2000)
    return () => clearInterval(timer)
  }, [captureRunning, data, error])

  async function mutate(action: () => Promise<unknown>) {
    setBusy(true); setActionError(null)
    try {
      await action()
    } catch (cause) {
      console.error('Snapshot AI review action failed:', cause)
      setActionError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally {
      setBusy(false); setRefresh(value => value + 1)
    }
  }

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 20))
  const reportUrl = analysisSource(id, manual) + '/report'
  return <section className="records-panel" aria-label={t('aiReview')}>
    <div className="panel-heading"><h2>{t('aiReview')}</h2>
      <button className="text-button" disabled={busy} onClick={() => setRefresh(value => value + 1)}>{t('refresh')}</button>
    </div>
    <div className="worker-panel-body">
      <p className="field-hint">{t(manual ? 'aiReviewDescription' : 'aiScheduledReviewDescription')}</p>
      {settings && !settings.configured && <p className="error-message" role="alert">{t('aiConfigurationError')}</p>}
      {settings && !settings.worker_enabled && <p className="error-message" role="alert">{t('aiDisabled')}</p>}
      {error && <p className="error-message" role="alert">{errorMessage(error)}</p>}
      {actionError && <p className="error-message" role="alert">{errorMessage(actionError)}</p>}
      {run?.analysis_error && <p className="error-message" role="alert">{t('aiEnqueueError', { reason: run.analysis_error })}</p>}
      {!run && <p className="empty-state">{t('aiChooseRun')}</p>}
      {run && !data && !error && <p role="status">{t('loadingTitle')}</p>}
      {run && data && <>
        <AnalysisProgress summary={data.summary} saved={run.saved} />
        {data.summary.total < run.saved && <p className="field-hint">{t('aiBatchCoverage', {
          total: data.summary.total, saved: run.saved,
        })}</p>}
      </>}
      {run && <div className="analysis-actions">
        <div className="configuration-actions">
          {manual && <button className={`button ${data?.summary.total ? 'secondary' : 'primary'}`}
            disabled={busy || !settings?.configured || captureRunning || !run.saved || data?.summary.stop_requested}
            onClick={() => { setLimit(20); setConfirm(true) }}>{t('aiAnalyzeBatch')}</button>}
          {data && (data.summary.stop_requested || data.summary.queued > 0 || data.summary.analyzing > 0 || captureRunning) &&
            <button className="button secondary" disabled={busy || (data.summary.stop_requested && !settings?.configured)}
              onClick={() => { void mutate(() => setAnalysisStopped(id, manual, !data.summary.stop_requested)) }}>
              {t(data.summary.stop_requested ? 'aiResume' : 'aiStop')}
            </button>}
          {Boolean(data?.summary.total) && <button className="button primary" onClick={() => setView(true)}>{t('aiResults')}</button>}
          {Boolean(data?.summary.failed) && <button className="button secondary" disabled={busy || !settings?.configured}
            onClick={() => { void mutate(() => retryAnalysis(id, manual)) }}>{t('aiRetry')}</button>}
        </div>
        {Boolean(data?.summary.total) && <a className="text-button" href={reportUrl} download>{t('aiDownload')}</a>}
      </div>}
      <p className="field-hint">{t('aiConsent')}</p>
      <details className="report-help analysis-automation">
        <summary>{t('aiAutomationSettings')}
          {settings && <span className="count-badge">{t(settings.auto_analyze ? 'aiAutoOn' : 'aiAutoOff')}</span>}
        </summary>
        <div className="report-help-content">
          <label className="report-option"><input type="checkbox" checked={settings?.auto_analyze ?? false}
            aria-describedby="analysis-auto-hint"
            disabled={busy || !settings || (!settings.configured && !settings.auto_analyze)}
            onChange={event => {
              const checked = event.target.checked
              setAutoSaved(false)
              void mutate(async () => { setSettings(await setAutoAnalysis(checked)); setAutoSaved(true) })
            }} /><span>{t('aiAuto')}</span></label>
          <p id="analysis-auto-hint">{t('aiAutoScope')}</p>
          {autoSaved && <p role="status">{t('aiSettingsSaved')}</p>}
        </div>
      </details>
      <details className="report-help"><summary>{t('aiHowItWorks')}</summary>
        <div className="report-help-content"><p>{t('aiHint')}</p><p>{t('aiWorkerHint')}</p></div>
      </details>
    </div>
    {confirm && <Dialog title={t('aiAnalyzeBatch')} subtitle={t('aiConsent')} onClose={() => setConfirm(false)}>
      <div className="dialog-body" id="dialog-description">
        <p>{t('aiPilotHint')}</p>
        <label>{t('aiScope')}<select value={limit === 20 ? '20' : 'all'}
          onChange={event => setLimit(event.target.value === '20' ? 20 : null)}>
          <option value="20">{t('aiPilot')}</option><option value="all">{t('aiAll')}</option>
        </select></label>
        <button className="button primary" disabled={busy} onClick={() => {
          setConfirm(false); void mutate(() => startBatchAnalysis(id, limit))
        }}>{t('aiStart')}</button>
      </div>
    </Dialog>}
    {view && <Dialog wide title={t('aiResults')} subtitle={id} onClose={() => setView(false)}>
      <div className="dialog-body" id="dialog-description">
        {error && <p className="error-message" role="alert">{errorMessage(error)}</p>}
        {actionError && <p className="error-message" role="alert">{errorMessage(actionError)}</p>}
        <div className="configuration-actions">
          <button className="text-button" onClick={() => setRefresh(value => value + 1)}>{t('refresh')}</button>
          <a className="text-button" href={reportUrl} download>{t('aiDownload')}</a>
        </div>
        {data && run && <AnalysisProgress summary={data.summary} saved={run.saved} />}
        <label>{t('result')} <select value={status} onChange={event => {
          const value = event.target.value
          if (value === '' || value === 'queued' || value === 'analyzing' || value === 'completed' || value === 'failed' || value === 'stopped') {
            setStatus(value); setPage(1)
          }
        }}>
          <option value="">{t('batchReportAllResults')}</option>
          {(['queued', 'analyzing', 'completed', 'failed', 'stopped'] as const).map(value =>
            <option key={value} value={value}>{t(analysisStatusKeys[value])}</option>)}
        </select></label>
        <div className="table-container"><table>
          <thead><tr><th scope="col">{t('shopCode')}</th><th scope="col">{t('cameraChannel')}</th>
            <th scope="col">{t('result')}</th><th scope="col">{t('reportDetails')}</th><th scope="col">{t('actions')}</th></tr></thead>
          <tbody>{data?.items.map(item => <tr key={item.id}>
            <td>{item.shop_code}</td><td>{item.channel}</td><td>{t(analysisStatusKeys[item.status])}
              {item.record && <span className="cell-subtitle">{t(outcomeKeys[item.record.outcome])}</span>}</td>
            <td>{item.message}
              {item.retry_at && <span className="cell-subtitle">{t('aiRetryAt', { time: formatDate(item.retry_at, true) })}</span>}
            </td><td>{item.record && <button className="text-button" disabled={busy} onClick={() => {
              void mutate(async () => { const record = await fetchRecord(item.id); setView(false); onSelect(record) })
            }}>{t('review')}</button>}</td>
          </tr>)}</tbody>
        </table>{!data?.items.length && <p className="empty-state">{t('batchReportNoResults')}</p>}</div>
        <footer className="table-footer"><span>{t('recordCount', { count: data?.total ?? 0 })}</span>
          <div className="pagination">
            <button className="icon-button" disabled={page === 1} aria-label={t('previousPage')}
              onClick={() => setPage(value => value - 1)}><Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} /></button>
            <span>{t('pageOf', { page, total: totalPages })}</span>
            <button className="icon-button" disabled={page >= totalPages} aria-label={t('nextPage')}
              onClick={() => setPage(value => value + 1)}><Icon name="chevron" size={16} /></button>
          </div>
        </footer>
      </div>
    </Dialog>}
  </section>
}
