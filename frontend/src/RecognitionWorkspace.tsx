import { useEffect, useRef, useState } from 'react'
import {
  authRequest, categoryKeys, checkKeys, fetchRecords, outcomeKeys, regionKeys, reviewKeys,
  type RecognitionPage, type RecognitionRecord,
} from './api'
import { EvidenceImage } from './components/EvidenceImage'
import { CameraConfiguration } from './components/CameraConfiguration'
import { InspectionReports } from './components/InspectionReports'
import { Icon } from './components/Icon'
import { RecordDrawer } from './components/RecordDrawer'
import { SnapshotWorker } from './components/SnapshotWorker'
import { UploadDialog } from './components/UploadDialog'
import { MonitoringOverview } from './components/MonitoringOverview'
import { CheckingRules } from './components/CheckingRules'
import { useLocale } from './localization'
import { isLanguage } from './messages'

const viewLabels = {
  overview: 'monitoringOverview', rules: 'checkingRules',
  records: 'recognitionRecords', configuration: 'cameraConfiguration',
  reports: 'inspectionReports', worker: 'snapshotWorker',
} as const

export function RecognitionWorkspace({ apiStatus, onLogout }: { apiStatus: string; onLogout: () => void }) {
  const { language, setLanguage, storageError, t, formatDate, errorMessage, checkExplanation } = useLocale()
  const [data, setData] = useState<RecognitionPage | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<Error | null>(null)
  const [search, setSearch] = useState('')
  const [outcome, setOutcome] = useState('')
  const [review, setReview] = useState('')
  const [region, setRegion] = useState('')
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [selected, setSelected] = useState<RecognitionRecord | null>(null)
  const [notice, setNotice] = useState<RecognitionRecord | null>(null)
  const [logoutError, setLogoutError] = useState(false)
  const [view, setView] = useState<keyof typeof viewLabels>('overview')
  const [reportBatch, setReportBatch] = useState<string | undefined>()
  const [reportTab, setReportTab] = useState<'capture' | 'analysis'>('analysis')
  const [smallScreen, setSmallScreen] = useState(() => typeof window !== 'undefined' && window.matchMedia('(max-width: 640px)').matches)
  const [sidebarPreference, setSidebarPreference] = useState<boolean | null>(null)
  const sidebarVisible = sidebarPreference ?? !smallScreen
  const sidebarToggleRef = useRef<HTMLButtonElement>(null)
  const focusSidebarToggle = useRef(false)
  const hasFilters = Boolean(search || outcome || review || region)
  const pageTitle = t(viewLabels[view])

  useEffect(() => {
    document.title = `${pageTitle} | RetailVision`
  }, [pageTitle])

  function openReports(batch?: string, tab: 'capture' | 'analysis' = 'analysis') {
    setReportBatch(batch)
    setReportTab(tab)
    setView('reports')
  }

  useEffect(() => {
    if (focusSidebarToggle.current) {
      sidebarToggleRef.current?.focus()
      focusSidebarToggle.current = false
    }
  }, [sidebarVisible])

  useEffect(() => {
    const query = window.matchMedia('(max-width: 640px)')
    const update = () => setSmallScreen(query.matches)
    update()
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    if (view !== 'records') return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      setLoading(true); setError(null)
      const params = new URLSearchParams({ page: String(page), page_size: '20', search })
      if (outcome) params.set('outcome', outcome)
      if (review) params.set('review_status', review)
      if (region) params.set('region', region)
      void fetchRecords(params, controller.signal).then(result => {
        if (!controller.signal.aborted) {
          const lastPage = Math.max(1, Math.ceil(result.total / result.page_size))
          if (page > lastPage) { setPage(lastPage); return }
          setData(result); setLoading(false)
        }
      }).catch(cause => {
        if (!controller.signal.aborted) {
          console.error('Recognition records failed to load:', cause)
          setError(cause instanceof Error ? cause : new Error(String(cause)))
          setLoading(false)
        }
      })
    }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [view, page, search, outcome, review, region, refresh])

  function startLoading() {
    setLoading(true)
    setError(null)
  }

  function reload() {
    startLoading()
    setRefresh(value => value + 1)
  }

  function clearFilters() {
    startLoading()
    setSearch(''); setOutcome(''); setReview(''); setRegion(''); setPage(1)
  }

  function uploaded(record: RecognitionRecord) {
    setUploadOpen(false); clearFilters(); setRefresh(value => value + 1)
    setNotice(record)
    setSelected(record)
  }

  const stats = [
    { label: 'totalInspections', value: data?.summary.total, icon: 'file', tone: 'teal', detail: 'allScreenshots' },
    { label: 'issuesFlagged', value: data?.summary.flagged, icon: 'alert', tone: 'amber', detail: 'possibleBlank' },
    { label: 'needsVerification', value: data?.summary.needs_review, icon: 'image', tone: 'blue', detail: 'incompleteChecks' },
    { label: 'awaitingReview', value: data?.summary.unreviewed, icon: 'clock', tone: 'purple', detail: 'noDecision' },
  ] as const
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const sidebarToggle = <button ref={sidebarToggleRef} type="button" className="sidebar-toggle" aria-controls="sidebar" aria-expanded={sidebarVisible}
    aria-label={t(sidebarVisible ? 'hideSidebar' : 'showSidebar')} title={t(sidebarVisible ? 'hideSidebar' : 'showSidebar')}
    onClick={() => { focusSidebarToggle.current = true; setSidebarPreference(!sidebarVisible) }}>
    <Icon name="sidebar" size={19} />
  </button>

  return <div className={`app-shell${sidebarVisible ? '' : ' sidebar-hidden'}`}>
    <a className="skip-link" href="#main-content">{t('skipLink')}</a>
    <aside id="sidebar" className="sidebar" hidden={!sidebarVisible}>
      <a className="brand" href="/"><span className="brand-mark"><Icon name="camera" size={25} /></span>
        <span>RETAIL<span className="brand-accent">VISION</span><small>{t('platform')}</small></span>
      </a>
      <div className="workspace-label">{t('operationsWorkspace')}</div>
      <nav aria-label={t('mainNavigation')}>
        {([
          ['overview', 'grid'], ['worker', 'clock'], ['rules', 'sliders'], ['reports', 'file'],
          ['records', 'shield'], ['configuration', 'camera'],
        ] as const).map(([item, icon]) => <a key={item} className={`nav-item${view === item ? ' active' : ''}`} href="#main-content"
          aria-current={view === item ? 'page' : undefined} title={t(viewLabels[item])}
          onClick={() => {
            if (item === 'reports') { setReportBatch(undefined); setReportTab('analysis') }
            setView(item)
            if (smallScreen) setSidebarPreference(false)
          }}>
          <Icon name={icon} size={19} /><span>{t(viewLabels[item])}</span>{view === item && <span className="nav-dot" />}
        </a>)}
      </nav>
      <div className="sidebar-scope">
        <p className="workspace-label">{t('currentWorkflow')}</p>
        <div><Icon name="clock" size={17} /><span>{t('monitoringAutomatic')}</span></div>
        <p>{t('monitoringSidebarHint')}</p>
      </div>
      <div className="sidebar-bottom"><Icon name="shield" size={18} /><div>{t('internalOperations')}<small>{t('regions')}</small></div>
        {sidebarVisible && sidebarToggle}
      </div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="topbar-leading">
          {!sidebarVisible && sidebarToggle}
          <div className="breadcrumb"><span>{t('operations')}</span><Icon name="chevron" size={14} /><strong>{t(viewLabels[view])}</strong></div>
        </div>
        <div className="topbar-actions">
        <button className="text-button" onClick={() => {
          setLogoutError(false)
          void authRequest('logout').then(onLogout).catch(error => {
            if (error instanceof Error && 'status' in error && error.status === 401) { onLogout(); return }
            console.error('Logout failed:', error)
            setLogoutError(true)
          })
        }}>{t('loginSignOut')}</button>
        <label className="language-selector"><span>{t('language')}</span>
          <select value={language} onChange={event => {
            if (isLanguage(event.target.value)) setLanguage(event.target.value)
          }}>
            <option value="en" lang="en">English</option>
            <option value="zh-Hant" lang="zh-Hant">繁體中文</option>
            <option value="zh-Hans" lang="zh-Hans">简体中文</option>
          </select>
        </label>
        <div className="connection-status" role="status" title={t(apiStatus === 'connected' ? 'apiConnected' : apiStatus === 'unavailable' ? 'apiUnavailable' : 'apiChecking')}>
          <span className={`status-dot ${view !== 'records' ? apiStatus === 'unavailable' ? 'offline' : apiStatus === 'checking' ? 'checking' : '' : error ? 'offline' : loading ? 'checking' : ''}`} />
          {view !== 'records' ? t(apiStatus === 'connected' ? 'apiConnected' : apiStatus === 'unavailable' ? 'apiUnavailable' : 'apiChecking') :
            t(error ? 'storageUnavailable' : loading ? 'syncing' : 'databaseConnected')}
        </div>
        </div>
      </header>
      <main id="main-content">
        {logoutError && <div className="error-message language-warning" role="alert">{t('loginLogoutError')}</div>}
        {storageError && <div className="error-message language-warning" role="alert">{t('languageStorageError')}</div>}
        {view === 'overview' ? <MonitoringOverview onSchedules={() => setView('worker')} onReports={openReports}
          onCameras={() => setView('configuration')} onRecords={() => setView('records')} /> :
          view === 'rules' ? <CheckingRules /> :
          view === 'configuration' ? <CameraConfiguration /> : view === 'reports' ? <InspectionReports onSelect={setSelected} initialBatch={reportBatch} initialTab={reportTab} /> :
          view === 'worker' ? <SnapshotWorker onOpenReports={batch => openReports(batch, 'capture')} /> : <>
        <div className="page-heading">
          <div><p className="eyebrow">{t('qualityAssurance')}</p><h1>{t('recognitionRecords')}</h1>
            <p className="page-description">{t('pageDescription')}</p>
          </div>
          <button className="button secondary" onClick={() => setUploadOpen(true)}>
            <Icon name="upload" size={18} />{t('uploadScreenshot')}
          </button>
        </div>
        {notice && <div className="notice success-notice" role="status"><Icon name="check" size={18} /><p>{t('uploadedNotice', { shop: notice.shop_code || t('notProvided'), camera: notice.camera_name || t('notProvided') })}</p>
          <button className="icon-button" aria-label={t('dismissNotification')} onClick={() => setNotice(null)}><Icon name="close" size={16} /></button>
        </div>}
        <section className="stats-grid" aria-label={t('summary')}>
          {stats.map(stat => <div className="stat-card" key={stat.label}>
            <div className="stat-top"><span>{t(stat.label)}</span><span className={`stat-icon ${stat.tone}`}><Icon name={stat.icon} size={19} /></span></div>
            <strong className="stat-value">{error || stat.value === undefined ? '—' : stat.value.toLocaleString(language)}</strong>
            <span className="stat-detail">{t(stat.detail)}</span>
          </div>)}
        </section>
        <div className="coverage-banner"><span className="coverage-icon"><Icon name="info" size={20} /></span>
          <div><strong>{t('coverageTitle')}</strong><p>{t('coverageDescription')}</p></div>
        </div>
        <section className="records-panel" aria-labelledby="records-title">
          <div className="panel-heading"><div className="section-heading"><h2 id="records-title">{t('history')}</h2>
            {data && !error && <span className="count-badge">{t('recordCount', { count: data.total.toLocaleString(language) })}</span>}</div>
            <button className="button secondary compact" disabled={loading} onClick={reload}>
              <Icon name="refresh" size={15} />{t('refresh')}
            </button>
          </div>
          <div className="filter-bar">
            <label className="search-field"><span className="sr-only">{t('searchRecords')}</span><Icon name="search" size={18} />
              <input placeholder={t('searchPlaceholder')} value={search} maxLength={100}
                onChange={event => { startLoading(); setSearch(event.target.value); setPage(1) }} />
            </label>
            <label><span className="sr-only">{t('filterOutcome')}</span><select value={outcome} onChange={event => { startLoading(); setOutcome(event.target.value); setPage(1) }}>
              <option value="">{t('allResults')}</option><option value="flagged">{t('issueFlagged')}</option><option value="needs_review">{t('needsVerification')}</option>
            </select></label>
            <label><span className="sr-only">{t('filterReview')}</span><select value={review} onChange={event => { startLoading(); setReview(event.target.value); setPage(1) }}>
              <option value="">{t('allReviews')}</option><option value="new">{t('unreviewed')}</option><option value="acknowledged">{t('acknowledged')}</option><option value="false_positive">{t('falsePositive')}</option>
            </select></label>
            <label><span className="sr-only">{t('filterRegion')}</span><select value={region} onChange={event => { startLoading(); setRegion(event.target.value); setPage(1) }}>
              <option value="">{t('allRegions')}</option><option value="Hong Kong">{t('hongKong')}</option><option value="Macau">{t('macau')}</option>
            </select></label>
            {hasFilters && <button className="text-button" onClick={clearFilters}>{t('reset')}</button>}
          </div>
          <div className="table-container" aria-busy={loading}>
            {loading ? <div className="empty-state" role="status"><span className="spinner large" /><h3>{t('loadingTitle')}</h3><p>{t('loadingDescription')}</p></div> :
              error ? <div className="empty-state error-state" role="alert"><span className="empty-icon"><Icon name="alert" size={28} /></span>
                <h3>{t('loadErrorTitle')}</h3><p>{errorMessage(error)}</p>
                <button className="button secondary" onClick={reload}><Icon name="refresh" size={16} />{t('tryAgain')}</button></div> :
                data?.records.length ? <table>
                  <caption className="sr-only">{t('tableCaption')}</caption>
                  <thead><tr><th scope="col">{t('cameraEvidence')}</th><th scope="col">{t('shopRegion')}</th><th scope="col">{t('uploadedHkt')}</th>
                    <th scope="col">{t('recognitionChecks')}</th><th scope="col">{t('result')}</th><th scope="col">{t('review')}</th><th scope="col"><span className="sr-only">{t('actions')}</span></th></tr></thead>
                  <tbody>{data.records.map(record => <tr key={record.id}>
                    <td><div className="camera-cell"><EvidenceImage id={record.id} alt={t('screenshotAlt', { camera: record.camera_name || t('notProvided') })} thumbnail />
                      <div><strong title={record.camera_name}>{record.camera_name || t('notProvided')}</strong><span title={record.filename}>{record.filename}</span></div></div></td>
                    <td><strong className="cell-title">{record.shop_code || t('notProvided')}</strong><span className="cell-subtitle">{t(regionKeys[record.region])}</span></td>
                    <td><time className="date-cell" dateTime={record.uploaded_at}>{formatDate(record.uploaded_at)}</time>
                      <span className="cell-subtitle">{record.width} × {record.height}</span></td>
                    <td><div className="table-checks">{record.checks.map(check =>
                      <span key={check.category} className={`mini-check check-${check.status}`} title={`${t(checkKeys[check.status])}: ${checkExplanation(record, check)}`}>
                        <span className="check-dot" />{t(categoryKeys[check.category])}
                        <span className="sr-only">: {t(checkKeys[check.status])}</span>
                      </span>)}</div></td>
                    <td><span className={`badge ${record.outcome}`}>{t(outcomeKeys[record.outcome])}</span></td>
                    <td><span className={`review-status review-${record.review_status}`}><span />{t(reviewKeys[record.review_status])}</span></td>
                    <td><button className="row-action" aria-label={t('reviewRecord', { shop: record.shop_code || t('notProvided'), camera: record.camera_name || t('notProvided') })} onClick={() => setSelected(record)}>
                      {t('review')}<Icon name="chevron" size={15} /></button></td>
                  </tr>)}</tbody>
                </table> :
                  <div className="empty-state"><span className="empty-icon"><Icon name={hasFilters ? 'search' : 'image'} size={29} /></span>
                    <h3>{t(hasFilters ? 'noMatches' : 'emptyTitle')}</h3>
                    <p>{t(hasFilters ? 'noMatchesDescription' : 'emptyDescription')}</p>
                    <button className="button primary" onClick={hasFilters ? clearFilters : () => setView('worker')}>
                      {t(hasFilters ? 'resetFilters' : 'monitoringSetUp')}</button>
                  </div>}
          </div>
          <footer className="table-footer"><span>{loading ? t('updating') : error ? t('recordsUnavailable') :
            data && data.total > 0 ? t('showingRecords', { start: (page - 1) * 20 + 1, end: Math.min(page * 20, data.total), total: data.total }) : t('recordCount', { count: 0 })}</span>
            <div className="pagination"><button className="icon-button" aria-label={t('previousPage')} disabled={loading || !!error || page === 1}
              onClick={() => { startLoading(); setPage(value => value - 1) }}><Icon name="chevron" size={16} style={{ transform: 'rotate(180deg)' }} /></button>
              <span>{t('pageOf', { page, total: totalPages })}</span><button className="icon-button" aria-label={t('nextPage')}
                disabled={loading || !!error || page >= totalPages} onClick={() => { startLoading(); setPage(value => value + 1) }}><Icon name="chevron" size={16} /></button></div>
          </footer>
        </section>
        <footer className="workspace-footer"><span><Icon name="shield" size={14} />{t('evidenceStored')}</span><span>{t('datesHkt')}</span></footer>
        </>}
      </main>
    </div>
    {uploadOpen && <UploadDialog onClose={() => setUploadOpen(false)} onUploaded={uploaded} />}
    {selected && <RecordDrawer key={selected.id} record={selected} onClose={() => setSelected(null)}
      onSaved={record => { setSelected(record); reload() }} />}
  </div>
}
