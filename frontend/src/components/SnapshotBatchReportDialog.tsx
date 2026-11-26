import { useEffect, useState } from 'react'
import { fetchSnapshotBatchReport, inspectionStatusKeys, regionKeys, type SnapshotBatchReport } from '../api'
import { useLocale } from '../localization'
import { Dialog } from './Dialog'
import { Icon } from './Icon'

export function SnapshotBatchReportDetails({ report }: { report: SnapshotBatchReport }) {
  const { t, formatDate } = useLocale()
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const filtered = report.items.filter(item => !status || item.status === status)
  const totalPages = Math.max(1, Math.ceil(filtered.length / 20))
  const rows = filtered.slice((page - 1) * 20, page * 20)
  const count = (value: string) => report.items.filter(item => item.status === value).length
  return <>
    <p>{t(inspectionStatusKeys[report.status] ?? 'runUnknown')} · {t('reportCounts', {
      total: report.items.length, completed: count('completed'), failed: count('failed'),
      skipped: count('skipped'), interrupted: count('interrupted'),
    })}</p>
    <p>{t('captureSaved', { count: report.items.filter(item => item.has_evidence).length })}</p>
    <p>{t('batchReportStarted', { time: formatDate(report.started_at, true) })}</p>
    <p>{t('batchReportFinished', { time: formatDate(report.finished_at, true) })}</p>
    {report.error && <p className="error-message" role="alert">{t('batchFailureDetail', { reason: report.error })}</p>}
    <div className="filter-bar"><label>{t('result')} <select value={status} onChange={event => {
      setStatus(event.target.value); setPage(1)
    }}>
      <option value="">{t('batchReportAllResults')}</option>
      {['completed', 'failed', 'skipped', 'interrupted', 'pending', 'capturing', 'captured'].map(value =>
        <option key={value} value={value}>{t(inspectionStatusKeys[value] ?? 'runUnknown')}</option>)}
    </select></label></div>
    <div className="table-container">
      <table><caption className="sr-only">{t('batchViewReport')}</caption>
        <thead><tr><th scope="col">{t('shopCode')}</th><th scope="col">{t('region')}</th>
          <th scope="col">{t('cameraChannel')}</th><th scope="col">{t('result')}</th>
          <th scope="col">{t('captureTime')}</th><th scope="col">{t('reportDetails')}</th></tr></thead>
        <tbody>{rows.map(item => <tr key={item.id}>
          <td>{item.shop_code}</td><td>{item.region === 'Hong Kong' || item.region === 'Macau' ?
            t(regionKeys[item.region]) : item.region}</td>
          <td>{item.channel ? `${item.channel}: ${item.camera_name}` : '—'}</td>
          <td>{t(inspectionStatusKeys[item.status] ?? 'runUnknown')}</td>
          <td>{formatDate(item.captured_at, true)}</td><td>{item.message}</td>
        </tr>)}</tbody>
      </table>
      {!rows.length && <p className="empty-state">{t('batchReportNoResults')}</p>}
    </div>
    <footer className="table-footer"><span>{t('recordCount', { count: filtered.length })}</span>
      <div className="pagination">
        <button className="icon-button" disabled={page === 1} aria-label={t('previousPage')}
          onClick={() => setPage(value => value - 1)}>
          <Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} />
        </button>
        <span>{t('pageOf', { page, total: totalPages })}</span>
        <button className="icon-button" disabled={page >= totalPages} aria-label={t('nextPage')}
          onClick={() => setPage(value => value + 1)}><Icon name="chevron" size={16} /></button>
      </div>
    </footer>
  </>
}

export function SnapshotBatchReportDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, errorMessage } = useLocale()
  const [report, setReport] = useState<SnapshotBatchReport | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void fetchSnapshotBatchReport(id, controller.signal).then(result => {
      if (controller.signal.aborted) return
      setReport(result); setLoading(false)
    }).catch(cause => {
      if (controller.signal.aborted) return
      console.error('Snapshot batch report failed to load:', cause)
      setError(cause instanceof Error ? cause : new Error(String(cause))); setLoading(false)
    })
    return () => controller.abort()
  }, [id, refresh])

  return <Dialog wide title={t('batchViewReport')} subtitle={id} onClose={onClose}>
    <div className="dialog-body" id="dialog-description">
      <p className="field-hint">{t('batchReportViewHint')}</p>
      <div className="configuration-actions">
        <button className="button secondary" disabled={loading} onClick={() => {
          setLoading(true); setError(null); setRefresh(value => value + 1)
        }}><Icon name="refresh" size={16} />{t('refresh')}</button>
        <a className="text-button" href={`/api/snapshot-batches/${encodeURIComponent(id)}/report`}
          download={`snapshot-batch-${id}.json`}>{t('batchDownloadReport')}</a>
      </div>
      {loading ? <p role="status">{t('loadingTitle')}</p> : error ?
        <p className="error-message" role="alert">{errorMessage(error)}</p> :
        report && <SnapshotBatchReportDetails key={`${id}:${refresh}`} report={report} />}
    </div>
  </Dialog>
}
