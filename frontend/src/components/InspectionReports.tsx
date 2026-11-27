import { useEffect, useState } from 'react'
import { fetchInspectionItems, fetchInspectionRuns, fetchRecord, inspectionStatusKeys as statusKeys, setSnapshotChannelExcluded, startSnapshotBatch, stopSnapshotBatchBrand, type CameraBrandFilter, type InspectionItem, type InspectionItemPage, type InspectionRun, type RecognitionRecord } from '../api'
import { useLocale } from '../localization'
import { Icon } from './Icon'
import { Dialog } from './Dialog'
import { EvidenceImage } from './EvidenceImage'
import { SnapshotBatchReportDialog } from './SnapshotBatchReportDialog'
import { SnapshotAnalysis } from './SnapshotAnalysis'

const runKey = (run: InspectionRun) => run.id ?? run.run_date

export function InspectionProgress({ run, manual, onViewReport, onStop, stopBusy }: {
  run: InspectionRun; manual: boolean; onViewReport?: (id: string) => void
  onStop?: (brand: Exclude<CameraBrandFilter, ''>) => void; stopBusy?: boolean
}) {
  const { t } = useLocale()
  const id = run.id
  const processed = run.completed + run.failed + run.skipped
  return <div className="worker-panel-body">
    {run.brand_progress?.length ? <div className="capture-brand-progress">
      {run.brand_progress.map(group => {
        const name = group.brand === 'DAHAU' ? 'Dahua' : group.brand === 'HIKVISION' ? 'Hikvision' : t('captureBrandUnknown')
        const done = group.completed + group.failed + group.skipped
        const requested = group.brand !== 'UNKNOWN' && (run.stop_requested?.includes(group.brand) ?? false)
        const canStop = group.brand !== 'UNKNOWN' && manual && run.status === 'running' &&
          onStop && run.capture_brands?.includes(group.brand)
        return <section className="capture-brand-card" key={group.brand} aria-label={name}>
          <div className="capture-brand-heading"><strong>{name}</strong>
            <span>{t('captureProgress', { done, total: group.total })}</span></div>
          <progress max={Math.max(1, group.total)} value={done}
            aria-label={t('captureBrandProgressLabel', { brand: name })} />
          <p role="status">{t('captureBrandCounts', {
            saved: group.saved, failed: group.failed, skipped: group.skipped,
            stopped: group.stopped, interrupted: group.interrupted,
          })}</p>
          {group.brand === 'UNKNOWN' && <p className="field-hint">{t('captureBrandUnknownHint')}</p>}
          {canStop && group.brand !== 'UNKNOWN' && <button className="button secondary" disabled={stopBusy || requested}
            onClick={() => onStop(group.brand === 'HIKVISION' ? 'HIKVISION' : 'DAHAU')}>
            {t(requested ? 'captureStoppingBrand' : 'captureStopBrand', { brand: name })}
          </button>}
        </section>
      })}
    </div> : <>
      <label>{t('captureProgress', { done: processed, total: run.total })}
        <progress max={Math.max(1, run.total)} value={processed} aria-label={t('captureProgressLabel')} />
      </label>
      <p role="status">{t('captureSaved', { count: run.saved })}</p>
      {Boolean(run.stopped) && <p role="status">{t('captureStoppedCount', { count: run.stopped ?? 0 })}</p>}
      <p className="field-hint">{t('captureBrandUnknownHint')}</p>
    </>}
    {!run.brand_progress?.length && manual && run.status === 'running' && onStop && <div className="configuration-actions">
      {run.capture_brands?.map(brand => {
        const requested = run.stop_requested?.includes(brand) ?? false
        const name = brand === 'DAHAU' ? 'Dahua' : 'Hikvision'
        return <button key={brand} className="button secondary" disabled={stopBusy || requested}
          onClick={() => onStop(brand)}>{t(requested ? 'captureStoppingBrand' : 'captureStopBrand', { brand: name })}</button>
      })}
    </div>}
    {Boolean(run.stop_requested?.length) && run.status === 'running' && <p role="status">{t('captureStoppingHint')}</p>}
    {run.error && <p className="error-message" role="alert">{t('batchFailureDetail', { reason: run.error })}</p>}
    {manual && id && <div className="configuration-actions">
      {onViewReport && <button className="text-button" onClick={() => onViewReport(id)}>{t('batchViewReport')}</button>}
      <a className="text-button" href={`/api/snapshot-batches/${id}/report`}
        download={`snapshot-batch-${id}.json`}>{t('batchDownloadReport')}</a>
    </div>}
  </div>
}

export function InspectionReports({ onSelect, onSchedules, initialBatch, initialTab = 'analysis' }: {
  onSelect: (record: RecognitionRecord) => void; onSchedules: () => void; initialBatch?: string; initialTab?: 'capture' | 'analysis'
}) {
  const { t, formatDate, errorMessage } = useLocale()
  const [runs, setRuns] = useState<InspectionRun[]>([])
  const [selected, setSelected] = useState(initialBatch ?? '')
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<InspectionItemPage | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [recordError, setRecordError] = useState(false)
  const [preview, setPreview] = useState<{ url: string; name: string; filename: string } | null>(null)
  const [manual, setManual] = useState(true)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<Error | null>(null)
  const [stopBusy, setStopBusy] = useState(false)
  const [stopError, setStopError] = useState<Error | null>(null)
  const [confirmBatch, setConfirmBatch] = useState(false)
  const [captureBrand, setCaptureBrand] = useState<CameraBrandFilter>('')
  const [recheckSkipped, setRecheckSkipped] = useState(false)
  const [exclusionBusy, setExclusionBusy] = useState<string | null>(null)
  const [exclusionError, setExclusionError] = useState<Error | null>(null)
  const [exclusionSaved, setExclusionSaved] = useState(false)
  const [viewReport, setViewReport] = useState<string | null>(null)
  const [tab, setTab] = useState<'capture' | 'analysis'>(initialTab)
  const run = runs.find(entry => runKey(entry) === selected)
  const itemRevision = run ? `${run.status}:${run.completed}:${run.failed}:${run.skipped}:${run.interrupted}:${run.stopped}:${run.saved}` : ''

  useEffect(() => {
    const controller = new AbortController()
    void fetchInspectionRuns(controller.signal, manual).then(result => {
      if (controller.signal.aborted) return
      setRuns(result)
      setError(null)
      setSelected(current => result.some(run => runKey(run) === current) ? current : result[0] ? runKey(result[0]) : '')
      setLoading(false)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Inspection runs failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
      setLoading(false)
    })
    return () => controller.abort()
  }, [refresh, manual])

  useEffect(() => {
    if (error || !runs.some(run => run.status === 'running')) return
    const timer = setInterval(() => setRefresh(value => value + 1), 2000)
    return () => clearInterval(timer)
  }, [runs, error])

  useEffect(() => {
    if (!selected || tab !== 'capture') return
    const controller = new AbortController()
    void fetchInspectionItems(selected, page, controller.signal, manual).then(result => {
      if (!controller.signal.aborted) setItems(result)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Inspection items failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    })
    return () => controller.abort()
  }, [selected, page, refresh, manual, itemRevision, tab])

  function reload() {
    setLoading(true); setError(null); setRefresh(value => value + 1)
  }

  async function captureAll() {
    setConfirmBatch(false); setStarting(true); setStartError(null)
    try {
      const batch = await startSnapshotBatch(recheckSkipped, captureBrand)
      setRecheckSkipped(false)
      setManual(true); setRuns([batch]); setSelected(batch.id); setItems(null); setPage(1); setTab('capture')
      setError(null); setRefresh(value => value + 1)
    } catch (cause) {
      console.error('Snapshot batch failed to start:', cause)
      setStartError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally {
      setStarting(false)
    }
  }

  async function toggleExclusion(item: InspectionItem) {
    setExclusionBusy(item.id); setExclusionError(null); setExclusionSaved(false)
    try {
      const result = await setSnapshotChannelExcluded(selected, item.id, !item.excluded)
      setItems(current => current ? { ...current, items: current.items.map(entry =>
        entry.id === item.id ? { ...entry, excluded: result.skip } : entry) } : current)
      setExclusionSaved(true)
    } catch (cause) {
      console.error('Channel exclusion update failed:', cause)
      setExclusionError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally {
      setExclusionBusy(null)
    }
  }

  async function stopCapture(brand: Exclude<CameraBrandFilter, ''>) {
    if (!run?.id) return
    setStopBusy(true); setStopError(null)
    try {
      await stopSnapshotBatchBrand(run.id, brand)
    } catch (cause) {
      console.error('Snapshot capture stop failed:', cause)
      setStopError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally {
      setStopBusy(false); setRefresh(value => value + 1)
    }
  }

  const totalPages = items ? Math.max(1, Math.ceil(items.total / items.page_size)) : 1
  return <>
    <div className="page-heading"><div><p className="eyebrow">{t('operations')}</p><h1>{t('inspectionReports')}</h1>
      <p className="page-description">{t('inspectionReportsDescription')}</p></div>
      <div className="configuration-actions">
        <button className="button secondary" onClick={reload} disabled={loading}><Icon name="refresh" size={16} />{t('refresh')}</button>
      </div>
    </div>
    <section className="inspection-actions" aria-label={t('inspectionStartOptions')}>
      <div className="inspection-action">
        <div><h2>{t('manualCaptureTitle')}</h2><p>{t('manualCaptureHint')}</p></div>
        <button className="button primary" disabled={starting || (manual && runs.some(run => run.status === 'running'))}
          onClick={() => setConfirmBatch(true)}><Icon name="camera" size={16} />{t(starting ? 'batchStarting' : 'manualCaptureStart')}</button>
      </div>
      <div className="inspection-action">
        <div><h2>{t('automaticCheckTitle')}</h2><p>{t('automaticCheckHint')}</p></div>
        <button className="button secondary" onClick={onSchedules}><Icon name="clock" size={16} />{t('monitoringManageSchedules')}</button>
      </div>
    </section>
    {startError && <div className="error-message inspection-start-error" role="alert">{errorMessage(startError)}</div>}
    <div className="inspection-results-heading"><h2>{t('inspectionResultsTitle')}</h2><p>{t('inspectionResultsHint')}</p></div>
    <div className="report-tabs" role="tablist" aria-label={t('reportViews')}>
      {(['capture', 'analysis'] as const).map(value => <button key={value} type="button" role="tab"
        id={`inspection-tab-${value}`} aria-controls={`inspection-panel-${value}`}
        aria-selected={tab === value} tabIndex={tab === value ? 0 : -1}
        onClick={() => setTab(value)} onKeyDown={event => {
          let next: typeof tab
          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') next = tab === 'capture' ? 'analysis' : 'capture'
          else if (event.key === 'Home') next = 'capture'
          else if (event.key === 'End') next = 'analysis'
          else return
          event.preventDefault(); setTab(next); document.getElementById(`inspection-tab-${next}`)?.focus()
        }}>{t(value === 'capture' ? 'reportCaptureTab' : 'aiReview')}</button>)}
    </div>
    <section className="records-panel report-selection" aria-label={t('reportSelection')}>
    <div className="filter-bar"><label>{t('batchRunType')} <select value={manual ? 'manual' : 'scheduled'}
      onChange={event => {
        setManual(event.target.value === 'manual'); setSelected(''); setItems(null); setRuns([])
        setPage(1); setLoading(true); setError(null); setStartError(null)
      }}>
      <option value="scheduled">{t('batchScheduledRuns')}</option>
      <option value="manual">{t('batchManualRuns')}</option>
    </select></label>
      <label>{t(manual ? 'batchRun' : 'reportDate')} <select value={selected} disabled={!runs.length}
        onChange={event => { setSelected(event.target.value); setItems(null); setPage(1); setError(null) }}>
        {!runs.length && <option value="">{t(loading ? 'loadingTitle' : 'reportChooseRun')}</option>}
        {runs.map(entry => <option key={runKey(entry)} value={runKey(entry)}>
          {manual ? `${entry.schedule_name ? `${entry.schedule_name} · ` : ''}${formatDate(entry.started_at, true)} · ${entry.id?.slice(0, 8)}` : entry.run_date}
        </option>)}
      </select></label>
      {run && <span className="badge">{t(statusKeys[run.status] ?? 'runUnknown')}</span>}
    </div>
    </section>
    {error && <div className="error-message" role="alert">{errorMessage(error)}</div>}
    {stopError && <div className="error-message" role="alert">{errorMessage(stopError)}</div>}
    <div role="tabpanel" id="inspection-panel-analysis" aria-labelledby="inspection-tab-analysis" hidden={tab !== 'analysis'}>
      {tab === 'analysis' && <SnapshotAnalysis key={`${manual}:${selected}`} run={run} manual={manual} onSelect={onSelect} />}
    </div>
    <div role="tabpanel" id="inspection-panel-capture" aria-labelledby="inspection-tab-capture" hidden={tab !== 'capture'}>
    {exclusionError && <div className="error-message" role="alert">{errorMessage(exclusionError)}</div>}
    {exclusionSaved && <p role="status">{t('batchExclusionSaved')}</p>}
    {recordError && <div className="error-message" role="alert">{t('reportRecordError')}</div>}
    <details className="report-help"><summary>{t('reportCaptureHelp')}</summary>
      <div className="report-help-content">
        <p>{t('batchArchiveHint')}</p><p>{t('batchSkipHint')}</p><p>{t('captureProgressHint')}</p>
      </div>
    </details>
    <section className="records-panel report-capture-panel" aria-label={t('inspectionReports')}>
      <div className="panel-heading">
        <h2>{t('reportCaptureTab')}</h2>
        {run && <span className="field-hint">{t('reportCounts', {
          total: run.total, completed: run.completed, failed: run.failed, skipped: run.skipped, interrupted: run.interrupted,
        })}</span>}
      </div>
      {run && <InspectionProgress run={run} manual={manual} onViewReport={setViewReport}
        onStop={brand => { void stopCapture(brand) }} stopBusy={stopBusy} />}
      {!loading && !error && !runs.length && <div className="empty-state">{t(manual ? 'batchEmpty' : 'reportEmpty')}</div>}
      {run && <div className="table-container">
        <table><thead><tr><th scope="col">{t('shopCode')}</th><th scope="col">{t('cameraChannel')}</th>
          <th scope="col">{t('result')}</th><th scope="col">{t('captureTime')}</th>
          <th scope="col">{t('reportDetails')}</th><th scope="col">{t('actions')}</th></tr></thead>
          <tbody>{items?.items.map(item => <tr key={item.id}>
            <td>{item.shop_code}</td><td>{item.channel ? `${item.channel}: ${item.camera_name}` : '—'}</td>
            <td>{t(statusKeys[item.status] ?? 'runUnknown')}</td><td>{formatDate(item.captured_at, true)}</td>
            <td>{item.coverage_method === 'recorder_titles' ? t('coverageTitles') :
              item.coverage_method === 'count_fallback' ? t('coverageFallback') :
              item.coverage_method === 'configured_count' ? t('coverageConfiguredCount') :
              item.coverage_method === 'excluded' ? t('coverageExcluded') : t('coverageNone')}
              {item.message && <span className="cell-subtitle">{item.message}</span>}</td><td>
              {manual && item.can_exclude && <button className="text-button" disabled={exclusionBusy !== null || run.status === 'running'}
                onClick={() => { void toggleExclusion(item) }}>{t(item.excluded ? 'batchEnableChannel' : 'batchSkipChannel')}</button>}
              {item.has_evidence && <button className="text-button" onClick={() => setPreview({
                url: manual ? `/api/snapshot-batches/${selected}/items/${item.id}/evidence` : `/api/inspection-runs/items/${item.id}/evidence`,
                name: `${item.shop_code} · ${item.camera_name}`,
                filename: `snapshot-${item.id}.jpeg`,
              })}>{t('snapshotPreview')}</button>}
              {item.has_evidence && <a className="text-button" href={manual ? `/api/snapshot-batches/${selected}/items/${item.id}/evidence` : `/api/inspection-runs/items/${item.id}/evidence`}
                download={`snapshot-${item.id}.jpeg`}>{t('reportEvidence')}</a>}
              {item.has_record && <button className="text-button" onClick={() => {
                setRecordError(false)
                void fetchRecord(item.id).then(onSelect).catch(cause => {
                  console.error('Inspection record failed to load:', cause); setRecordError(true)
                })
              }}>{t('review')}</button>}
            </td>
          </tr>)}</tbody>
        </table>
        {!items && !error && <div className="empty-state" role="status">{t('loadingTitle')}</div>}
      </div>}
      {run && <footer className="table-footer"><span>{items ? t('recordCount', { count: items.total }) : ''}</span>
        <div className="pagination">
          <button className="icon-button" disabled={!items || page === 1} aria-label={t('previousPage')}
            onClick={() => { setItems(null); setPage(value => value - 1) }}><Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} /></button>
          <span>{t('pageOf', { page, total: totalPages })}</span>
          <button className="icon-button" disabled={!items || page >= totalPages} aria-label={t('nextPage')}
            onClick={() => { setItems(null); setPage(value => value + 1) }}><Icon name="chevron" size={16} /></button>
        </div></footer>}
    </section>
    </div>
    {viewReport && <SnapshotBatchReportDialog key={viewReport} id={viewReport} onClose={() => setViewReport(null)} />}
    {confirmBatch && <Dialog className="capture-confirmation" title={t('batchCaptureAll')} subtitle={t('batchConfirm')} onClose={() => setConfirmBatch(false)}>
      <div className="dialog-body" id="dialog-description">
        <label>{t('batchBrand')}<select value={captureBrand} onChange={event => {
          const value = event.target.value
          if (value === '' || value === 'DAHAU' || value === 'HIKVISION') setCaptureBrand(value)
        }}>
          <option value="">{t('batchBrandBoth')}</option>
          <option value="DAHAU">Dahua</option>
          <option value="HIKVISION">Hikvision</option>
        </select></label>
        <p>{t('batchArchiveHint')}</p>
        <p>{t('batchSkipHint')}</p>
        <label className="report-option"><input type="checkbox" checked={recheckSkipped} onChange={event => setRecheckSkipped(event.target.checked)} />
          {t('batchRecheckSkipped')}</label>
        <button className="button primary" onClick={() => { void captureAll() }}>{t('manualCaptureStart')}</button>
      </div>
    </Dialog>}
    {preview && <Dialog title={t('snapshotPreview')} subtitle={preview.name} onClose={() => setPreview(null)}>
      <div className="dialog-body" id="dialog-description">
        <EvidenceImage src={preview.url} alt={t('evidenceAlt', { camera: preview.name })} />
        <a className="text-button" href={preview.url} download={preview.filename}>{t('reportEvidence')}</a>
      </div>
    </Dialog>}
  </>
}
