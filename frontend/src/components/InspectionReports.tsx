import { useEffect, useState } from 'react'
import { fetchInspectionItems, fetchInspectionRuns, fetchRecord, inspectionStatusKeys as statusKeys, setSnapshotChannelExcluded, startSnapshotBatch, type InspectionItem, type InspectionItemPage, type InspectionRun, type RecognitionRecord } from '../api'
import { useLocale } from '../localization'
import { Icon } from './Icon'
import { Dialog } from './Dialog'
import { EvidenceImage } from './EvidenceImage'
import { SnapshotBatchReportDialog } from './SnapshotBatchReportDialog'
import { SnapshotAnalysis } from './SnapshotAnalysis'

const runKey = (run: InspectionRun) => run.id ?? run.run_date

export function InspectionProgress({ run, manual, onViewReport }: {
  run: InspectionRun; manual: boolean; onViewReport?: (id: string) => void
}) {
  const { t } = useLocale()
  const id = run.id
  const processed = run.completed + run.failed + run.skipped
  return <div className="worker-panel-body">
    <label>{t('captureProgress', { done: processed, total: run.total })}
      <progress max={Math.max(1, run.total)} value={processed} aria-label={t('captureProgressLabel')} />
    </label>
    <p role="status">{t('captureSaved', { count: run.saved })}</p>
    {run.error && <p className="error-message" role="alert">{t('batchFailureDetail', { reason: run.error })}</p>}
    {manual && id && <div className="configuration-actions">
      {onViewReport && <button className="text-button" onClick={() => onViewReport(id)}>{t('batchViewReport')}</button>}
      <a className="text-button" href={`/api/snapshot-batches/${id}/report`}
        download={`snapshot-batch-${id}.json`}>{t('batchDownloadReport')}</a>
    </div>}
    <p className="field-hint">{t('captureProgressHint')}</p>
  </div>
}

export function InspectionReports({ onSelect }: { onSelect: (record: RecognitionRecord) => void }) {
  const { t, formatDate, errorMessage } = useLocale()
  const [runs, setRuns] = useState<InspectionRun[]>([])
  const [selected, setSelected] = useState('')
  const [page, setPage] = useState(1)
  const [items, setItems] = useState<InspectionItemPage | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [recordError, setRecordError] = useState(false)
  const [preview, setPreview] = useState<{ url: string; name: string; filename: string } | null>(null)
  const [manual, setManual] = useState(false)
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<Error | null>(null)
  const [confirmBatch, setConfirmBatch] = useState(false)
  const [recheckSkipped, setRecheckSkipped] = useState(false)
  const [exclusionBusy, setExclusionBusy] = useState<string | null>(null)
  const [exclusionError, setExclusionError] = useState<Error | null>(null)
  const [exclusionSaved, setExclusionSaved] = useState(false)
  const [viewReport, setViewReport] = useState<string | null>(null)
  const run = runs.find(entry => runKey(entry) === selected)
  const itemRevision = run ? `${run.status}:${run.completed}:${run.failed}:${run.skipped}:${run.interrupted}:${run.saved}` : ''

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
    if (!selected) return
    const controller = new AbortController()
    void fetchInspectionItems(selected, page, controller.signal, manual).then(result => {
      if (!controller.signal.aborted) setItems(result)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Inspection items failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    })
    return () => controller.abort()
  }, [selected, page, refresh, manual, itemRevision])

  function reload() {
    setLoading(true); setError(null); setRefresh(value => value + 1)
  }

  async function captureAll() {
    setConfirmBatch(false); setStarting(true); setStartError(null)
    try {
      const batch = await startSnapshotBatch(recheckSkipped)
      setRecheckSkipped(false)
      setManual(true); setRuns([batch]); setSelected(batch.id); setItems(null); setPage(1)
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

  const totalPages = items ? Math.max(1, Math.ceil(items.total / items.page_size)) : 1
  return <>
    <div className="page-heading"><div><p className="eyebrow">{t('operations')}</p><h1>{t('inspectionReports')}</h1>
      <p className="page-description">{t('inspectionReportsDescription')}</p></div>
      <div className="configuration-actions">
        <button className="button primary" disabled={starting || (manual && runs.some(run => run.status === 'running'))}
          onClick={() => setConfirmBatch(true)}><Icon name="camera" size={16} />{t(starting ? 'batchStarting' : 'batchCaptureAll')}</button>
        <button className="button secondary" onClick={reload} disabled={loading}><Icon name="refresh" size={16} />{t('refresh')}</button>
      </div>
    </div>
    <p className="field-hint">{t('batchArchiveHint')}</p>
    <p className="field-hint">{t('batchSkipHint')}</p>
    <div className="filter-bar"><label>{t('batchRunType')} <select value={manual ? 'manual' : 'scheduled'}
      onChange={event => {
        setManual(event.target.value === 'manual'); setSelected(''); setItems(null); setRuns([])
        setPage(1); setLoading(true); setError(null); setStartError(null)
      }}>
      <option value="scheduled">{t('batchScheduledRuns')}</option>
      <option value="manual">{t('batchManualRuns')}</option>
    </select></label></div>
    {startError && <div className="error-message" role="alert">{errorMessage(startError)}</div>}
    {exclusionError && <div className="error-message" role="alert">{errorMessage(exclusionError)}</div>}
    {exclusionSaved && <p role="status">{t('batchExclusionSaved')}</p>}
    {run && <InspectionProgress run={run} manual={manual} onViewReport={setViewReport} />}
    <SnapshotAnalysis key={`${manual}:${selected}`} run={run} manual={manual} onSelect={onSelect} />
    {error && <div className="error-message" role="alert">{errorMessage(error)}</div>}
    {recordError && <div className="error-message" role="alert">{t('reportRecordError')}</div>}
    <section className="records-panel" aria-label={t('inspectionReports')}>
      <div className="panel-heading">
        <label>{t(manual ? 'batchRun' : 'reportDate')} <select value={selected} disabled={!runs.length}
          onChange={event => { setSelected(event.target.value); setItems(null); setPage(1); setError(null) }}>
          {runs.map(entry => <option key={runKey(entry)} value={runKey(entry)}>
            {manual ? `${formatDate(entry.started_at, true)} · ${entry.id?.slice(0, 8)}` : entry.run_date}
          </option>)}
        </select></label>
        {run && <span>{t(statusKeys[run.status] ?? 'runUnknown')} · {t('reportCounts', {
          total: run.total, completed: run.completed, failed: run.failed, skipped: run.skipped, interrupted: run.interrupted,
        })}</span>}
      </div>
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
    {viewReport && <SnapshotBatchReportDialog key={viewReport} id={viewReport} onClose={() => setViewReport(null)} />}
    {confirmBatch && <Dialog title={t('batchCaptureAll')} subtitle={t('batchConfirm')} onClose={() => setConfirmBatch(false)}>
      <div className="dialog-body" id="dialog-description">
        <p>{t('batchArchiveHint')}</p>
        <p>{t('batchSkipHint')}</p>
        <label><input type="checkbox" checked={recheckSkipped} onChange={event => setRecheckSkipped(event.target.checked)} />
          {t('batchRecheckSkipped')}</label>
        <button className="button primary" onClick={() => { void captureAll() }}>{t('batchCaptureAll')}</button>
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
