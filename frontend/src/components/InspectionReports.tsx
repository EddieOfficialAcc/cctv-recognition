import { useEffect, useState } from 'react'
import { fetchInspectionItems, fetchInspectionRuns, fetchRecord, type InspectionItemPage, type InspectionRun, type RecognitionRecord } from '../api'
import { useLocale } from '../localization'
import type { MessageKey } from '../messages'
import { Icon } from './Icon'
import { Dialog } from './Dialog'
import { EvidenceImage } from './EvidenceImage'

const statusKeys: Record<string, MessageKey> = {
  running: 'runRunning', completed: 'runCompleted', interrupted: 'runInterrupted', missed: 'runMissed',
  pending: 'runPending', captured: 'runCaptured', failed: 'runFailed', skipped: 'runSkipped',
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

  useEffect(() => {
    const controller = new AbortController()
    void fetchInspectionRuns(controller.signal).then(result => {
      if (controller.signal.aborted) return
      setRuns(result)
      setSelected(current => result.some(run => run.run_date === current) ? current : result[0]?.run_date ?? '')
      setLoading(false)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Inspection runs failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
      setLoading(false)
    })
    return () => controller.abort()
  }, [refresh])

  useEffect(() => {
    if (!selected) return
    const controller = new AbortController()
    void fetchInspectionItems(selected, page, controller.signal).then(result => {
      if (!controller.signal.aborted) setItems(result)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Inspection items failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause)))
    })
    return () => controller.abort()
  }, [selected, page, refresh])

  function reload() {
    setLoading(true); setError(null); setRefresh(value => value + 1)
  }

  const run = runs.find(entry => entry.run_date === selected)
  const totalPages = items ? Math.max(1, Math.ceil(items.total / items.page_size)) : 1
  return <>
    <div className="page-heading"><div><p className="eyebrow">{t('operations')}</p><h1>{t('inspectionReports')}</h1>
      <p className="page-description">{t('inspectionReportsDescription')}</p></div>
      <button className="button secondary" onClick={reload} disabled={loading}><Icon name="refresh" size={16} />{t('refresh')}</button>
    </div>
    {error && <div className="error-message" role="alert">{errorMessage(error)}</div>}
    {recordError && <div className="error-message" role="alert">{t('reportRecordError')}</div>}
    <section className="records-panel" aria-label={t('inspectionReports')}>
      <div className="panel-heading">
        <label>{t('reportDate')} <select value={selected} disabled={!runs.length}
          onChange={event => { setSelected(event.target.value); setItems(null); setPage(1); setError(null) }}>
          {runs.map(entry => <option key={entry.run_date} value={entry.run_date}>{entry.run_date}</option>)}
        </select></label>
        {run && <span>{t(statusKeys[run.status] ?? 'runUnknown')} · {t('reportCounts', {
          total: run.total, completed: run.completed, failed: run.failed, skipped: run.skipped, interrupted: run.interrupted,
        })}</span>}
      </div>
      {!loading && !error && !runs.length && <div className="empty-state">{t('reportEmpty')}</div>}
      {run && <div className="table-container">
        <table><thead><tr><th scope="col">{t('shopCode')}</th><th scope="col">{t('cameraChannel')}</th>
          <th scope="col">{t('result')}</th><th scope="col">{t('captureTime')}</th>
          <th scope="col">{t('reportDetails')}</th><th scope="col">{t('actions')}</th></tr></thead>
          <tbody>{items?.items.map(item => <tr key={item.id}>
            <td>{item.shop_code}</td><td>{item.channel ? `${item.channel}: ${item.camera_name}` : '—'}</td>
            <td>{t(statusKeys[item.status] ?? 'runUnknown')}</td><td>{formatDate(item.captured_at, true)}</td>
            <td>{item.coverage_method === 'recorder_titles' ? t('coverageTitles') :
              item.coverage_method === 'count_fallback' ? t('coverageFallback') :
              item.coverage_method === 'configured_count' ? t('coverageConfiguredCount') : t('coverageNone')}
              {item.message && <span className="cell-subtitle">{item.message}</span>}</td><td>
              {item.has_evidence && <button className="text-button" onClick={() => setPreview({
                url: `/api/inspection-runs/items/${item.id}/evidence`,
                name: `${item.shop_code} · ${item.camera_name}`,
                filename: `snapshot-${item.id}.jpg`,
              })}>{t('snapshotPreview')}</button>}
              {item.has_evidence && <a className="text-button" href={`/api/inspection-runs/items/${item.id}/evidence`}
                download={`snapshot-${item.id}.jpg`}>{t('reportEvidence')}</a>}
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
    {preview && <Dialog title={t('snapshotPreview')} subtitle={preview.name} onClose={() => setPreview(null)}>
      <div className="dialog-body" id="dialog-description">
        <EvidenceImage src={preview.url} alt={t('evidenceAlt', { camera: preview.name })} />
        <a className="text-button" href={preview.url} download={preview.filename}>{t('reportEvidence')}</a>
      </div>
    </Dialog>}
  </>
}
