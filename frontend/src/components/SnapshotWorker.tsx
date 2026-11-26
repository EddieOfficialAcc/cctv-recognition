import { useEffect, useState } from 'react'
import { fetchInspectionRuns, type InspectionRun } from '../api'
import { useLocale } from '../localization'
import { Icon } from './Icon'

export function SnapshotWorker({ onOpenReports }: { onOpenReports: () => void }) {
  const { t, errorMessage } = useLocale()
  const [latest, setLatest] = useState<InspectionRun | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const [mode, setMode] = useState<'capture' | 'analyze'>('capture')

  useEffect(() => {
    const controller = new AbortController()
    void fetchInspectionRuns(controller.signal).then(runs => {
      if (!controller.signal.aborted) {
        setLatest(runs[0] ?? null)
        setLoading(false)
      }
    }).catch(cause => {
      if (!controller.signal.aborted) {
        console.error('Snapshot worker run history failed to load:', cause)
        setError(cause instanceof Error ? cause : new Error(String(cause)))
        setLoading(false)
      }
    })
    return () => controller.abort()
  }, [refresh])

  const command = [
    'Set-Location .\\backend',
    "$env:SCHEDULED_INSPECTION_ENABLED = 'true'",
    `$env:SCHEDULED_INSPECTION_ANALYZE = '${mode === 'analyze'}'`,
    '.\\.venv\\Scripts\\python.exe -m app.scheduled_inspection',
  ].join('\n')

  return <>
    <div className="page-heading">
      <div><p className="eyebrow">{t('operations')}</p><h1>{t('snapshotWorker')}</h1>
        <p className="page-description">{t('workerSchedule')}</p></div>
      <div className="configuration-actions">
      <button className="button primary" onClick={onOpenReports}><Icon name="camera" size={16} />{t('batchOpenReports')}</button>
      <button className="button secondary" disabled={loading} onClick={() => {
        setError(null); setLoading(true); setRefresh(value => value + 1)
      }}><Icon name="refresh" size={16} />{t('refresh')}</button>
      </div>
    </div>
    <section className="records-panel" aria-labelledby="worker-latest">
      <div className="panel-heading"><h2 id="worker-latest">{t('workerLatestRun')}</h2></div>
      <div className="worker-panel-body">
        <p className="muted">{t('workerHistoryHint')}</p>
        {loading ? <p role="status">{t('loadingTitle')}</p> : error ?
          <p className="error-message" role="alert">{errorMessage(error)}</p> : latest ?
            <p><time dateTime={latest.run_date}>{latest.run_date}</time> · {t('reportCounts', {
              total: latest.total, completed: latest.completed, failed: latest.failed,
              skipped: latest.skipped, interrupted: latest.interrupted,
            })}</p> : <p>{t('reportEmpty')}</p>}
      </div>
    </section>
    <section className="records-panel" aria-labelledby="worker-setup">
      <div className="panel-heading"><h2 id="worker-setup">{t('workerSetup')}</h2></div>
      <div className="worker-panel-body">
        <p className="muted">{t('workerSetupHint')}</p>
        <p className="field-hint">{t('aiScheduledHint')}</p>
        <label>{t('workerMode')}<select value={mode} onChange={event => {
          setMode(event.target.value === 'analyze' ? 'analyze' : 'capture')
        }}>
          <option value="capture">{t('workerCaptureOnly')}</option>
          <option value="analyze">{t('workerWithAnalysis')}</option>
        </select></label>
        <label>{t('workerCommand')}<textarea readOnly rows={4} value={command} spellCheck={false} /></label>
      </div>
    </section>
  </>
}
