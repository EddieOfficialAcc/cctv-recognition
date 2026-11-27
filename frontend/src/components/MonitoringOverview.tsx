import { useEffect, useState } from 'react'
import {
  fetchAnalysisSettings, fetchInspectionRuns, fetchReviewSchedules, inspectionStatusKeys,
  type AnalysisSettings, type InspectionRun, type ReviewSchedulePage,
} from '../api'
import { useLocale } from '../localization'
import { Icon } from './Icon'

interface MonitoringData {
  schedules: ReviewSchedulePage
  runs: InspectionRun[]
  analysis: AnalysisSettings
}

export function MonitoringOverview({ onSchedules, onReports, onManualCapture, onCameras, onRecords }: {
  onSchedules: () => void
  onReports: (batchId?: string) => void
  onManualCapture: () => void
  onCameras: () => void
  onRecords: () => void
}) {
  const { t, formatDate, errorMessage } = useLocale()
  const [data, setData] = useState<MonitoringData | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    async function load() {
      try {
        const [schedules, runs, analysis] = await Promise.all([
          fetchReviewSchedules(controller.signal),
          fetchInspectionRuns(controller.signal, true),
          fetchAnalysisSettings(controller.signal),
        ])
        if (!controller.signal.aborted) {
          setData({ schedules, runs, analysis }); setError(null)
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          console.error('Monitoring overview failed to load:', cause)
          setError(cause instanceof Error ? cause : new Error(String(cause)))
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => { void load() }, 10000)
      }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [refresh])

  const enabled = data?.schedules.schedules.filter(schedule => schedule.enabled) ?? []
  const next = enabled.flatMap(schedule => schedule.next_run ? [{ ...schedule, next_run: schedule.next_run }] : [])
    .sort((a, b) => Date.parse(a.next_run) - Date.parse(b.next_run))[0]
  const runs = data ? [...data.runs].sort((a, b) =>
    (b.started_at ? Date.parse(b.started_at) : 0) - (a.started_at ? Date.parse(a.started_at) : 0)).slice(0, 4) : []
  const ready = data && data.schedules.scheduler_online && enabled.length > 0 &&
    data.analysis.configured && data.analysis.worker_enabled && data.analysis.worker_online
  const statusKey = !data || error ? 'monitoringUnknown' : !data.schedules.scheduler_online ? 'monitoringSchedulerOffline' :
    !enabled.length ? 'monitoringInactive' : !ready ? 'monitoringNeedsSetup' : 'monitoringReady'
  const steps = [
    { icon: 'camera', title: 'monitoringCapture', detail: 'monitoringCaptureHint' },
    { icon: 'shield', title: 'monitoringAnalyze', detail: 'monitoringAnalyzeHint' },
    { icon: 'file', title: 'monitoringReview', detail: 'monitoringReviewHint' },
  ] as const

  return <div className="monitoring-overview">
    <div className="page-heading">
      <div><p className="eyebrow">{t('monitoringEyebrow')}</p><h1>{t('monitoringOverview')}</h1>
        <p className="page-description">{t('monitoringDescription')}</p></div>
      <button className="button secondary" onClick={() => setRefresh(value => value + 1)}>
        <Icon name="refresh" size={16} />{t('refresh')}
      </button>
    </div>
    <section className="monitoring-hero" aria-labelledby="monitoring-title">
      <div className="monitoring-hero-copy">
        <span className="hero-label"><Icon name="clock" size={15} />{t('monitoringAutomatic')}</span>
        <h2 id="monitoring-title">{t('monitoringTitle')}</h2>
        <p>{t('monitoringIntro')}</p>
        <div className="configuration-actions">
          <button className="button primary" onClick={onSchedules}><Icon name="clock" size={17} />{t('monitoringManageSchedules')}</button>
          <button className="button hero-secondary" onClick={onManualCapture}><Icon name="camera" size={17} />{t('manualCaptureOpen')}</button>
          <button className="button hero-secondary" onClick={() => onReports()}>{t('monitoringViewResults')}<Icon name="chevron" size={15} /></button>
        </div>
      </div>
      <ol className="monitoring-flow" aria-label={t('schedulePolicyTitle')}>
        {steps.map((step, index) => <li key={step.title}>
          <span className="flow-icon"><Icon name={step.icon} size={21} /></span>
          <div><span className="flow-step">0{index + 1}</span><h3>{t(step.title)}</h3><p>{t(step.detail)}</p></div>
        </li>)}
      </ol>
    </section>
    <div className={`monitoring-status${ready && !error ? ' is-ready' : ''}`} role="status">
      <Icon name={ready && !error ? 'check' : 'info'} size={19} />
      <strong>{t(statusKey)}</strong>
      <span>{t('monitoringStatusScope')}</span>
    </div>
    {error ? <div className="error-message" role="alert">{errorMessage(error)}
      <button className="text-button" onClick={() => setRefresh(value => value + 1)}>{t('tryAgain')}</button>
    </div> : !data ? <div className="empty-state" role="status"><span className="spinner large" /><p>{t('monitoringLoading')}</p></div> : <>
      <section className="monitoring-metrics" aria-label={t('monitoringAutomationStatus')}>
        <article className="stat-card"><div className="stat-top"><span>{t('monitoringEnabledSchedules')}</span><Icon name="clock" size={19} /></div>
          <strong className="stat-value">{enabled.length}</strong><p className="stat-detail">{t('monitoringScheduleCount', { count: data.schedules.schedules.length })}</p>
          <button className="row-action" onClick={onSchedules}>{t('monitoringManageSchedules')}<Icon name="chevron" size={14} /></button>
        </article>
        <article className="stat-card"><div className="stat-top"><span>{t('scheduleNext')}</span><Icon name="camera" size={19} /></div>
          <strong className="next-check">{next?.next_run ? <time dateTime={next.next_run}>{formatDate(next.next_run)}</time> : t('monitoringNotScheduled')}</strong>
          <p className="stat-detail">{next ? next.name : t('monitoringEnableHint')}</p>
          <span className="metric-note">{t('monitoringTimezone')}</span>
        </article>
        <article className="stat-card"><div className="stat-top"><span>{t('monitoringAIService')}</span><Icon name="shield" size={19} /></div>
          <strong className="next-check">{t(!data.analysis.configured ? 'monitoringAIUnconfigured' :
            !data.analysis.worker_enabled ? 'monitoringAIDisabled' : data.analysis.worker_online ? 'monitoringAIOnline' : 'monitoringOffline')}</strong>
          <p className="stat-detail">{t('monitoringAIServiceHint')}</p>
          <span className="metric-note">{t(data.schedules.scheduler_online ? 'monitoringSchedulerOnline' : 'monitoringSchedulerOffline')}</span>
        </article>
      </section>
      {!enabled.length && <section className="monitoring-setup" aria-labelledby="setup-title">
        <span className="setup-icon"><Icon name="clock" size={25} /></span>
        <div><h2 id="setup-title">{t('monitoringSetupTitle')}</h2><p>{t('monitoringSetupHint')}</p></div>
        <button className="button primary" onClick={onSchedules}>{t('monitoringSetUp')}<Icon name="chevron" size={16} /></button>
      </section>}
      <section className="records-panel monitoring-runs" aria-labelledby="recent-runs-title">
        <div className="panel-heading"><div><p className="eyebrow">{t('monitoringActivity')}</p><h2 id="recent-runs-title">{t('monitoringRecentRuns')}</h2></div>
          <button className="row-action" onClick={() => onReports()}>{t('monitoringAllReports')}<Icon name="chevron" size={15} /></button></div>
        <p className="monitoring-panel-hint">{t('monitoringRunsHint')}</p>
        {runs.length ? <div className="monitoring-run-list">{runs.map(run => <article className="monitoring-run" key={run.id}>
          <span className={`run-symbol ${run.failed || run.status === 'failed' || run.status === 'interrupted' ? 'amber' : 'teal'}`}><Icon name="camera" size={20} /></span>
          <div className="run-description"><h3>{run.schedule_name || t('monitoringOnDemand')}</h3>
            <p>{run.started_at ? <time dateTime={run.started_at}>{formatDate(run.started_at)}</time> : t('notProvided')}</p>
            <p>{t('monitoringRunCounts', { saved: run.saved, failed: run.failed, skipped: run.skipped })}</p></div>
          <span className={`badge ${run.status === 'failed' || run.status === 'interrupted' ? 'flagged' : 'needs_review'}`}>{t(inspectionStatusKeys[run.status] ?? 'runUnknown')}</span>
          <button className="button secondary compact" onClick={() => onReports(run.id)}>{t('monitoringOpenResults')}<Icon name="chevron" size={14} /></button>
        </article>)}</div> : <div className="empty-state monitoring-empty"><span className="empty-icon"><Icon name="camera" size={27} /></span>
          <h3>{t('monitoringNoRuns')}</h3><p>{t('monitoringNoRunsHint')}</p></div>}
      </section>
    </>}
    <section className="monitoring-tools" aria-label={t('monitoringSupportingTools')}>
      <button onClick={onCameras}><Icon name="camera" size={20} /><span><strong>{t('cameraConfiguration')}</strong><small>{t('monitoringCamerasHint')}</small></span><Icon name="chevron" size={16} /></button>
      <button onClick={onRecords}><Icon name="file" size={20} /><span><strong>{t('recognitionRecords')}</strong><small>{t('monitoringRecordsHint')}</small></span><Icon name="chevron" size={16} /></button>
    </section>
    <p className="monitoring-disclaimer"><Icon name="info" size={15} />{t('coverageDescription')}</p>
  </div>
}
