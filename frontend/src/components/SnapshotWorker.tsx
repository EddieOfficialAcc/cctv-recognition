import { useEffect, useState, type FormEvent } from 'react'
import {
  deleteReviewSchedule, fetchReviewSchedules, inspectionStatusKeys, previewReviewSchedule, saveReviewSchedule,
  type ReviewSchedule, type ReviewScheduleInput, type ReviewSchedulePage,
} from '../api'
import { useLocale } from '../localization'
import { Dialog } from './Dialog'
import { Icon } from './Icon'

const frequencies = ['hourly', 'daily', 'weekly', 'monthly', 'yearly', 'advanced'] as const
type Frequency = typeof frequencies[number]
interface ScheduleTiming {
  frequency: Frequency
  time: string
  minute: string
  weekdays: number[]
  days: string
  months: number[]
}

function scheduleExpression(timing: ScheduleTiming, advanced: string) {
  const [hour, minute] = timing.time.split(':').map(Number)
  switch (timing.frequency) {
    case 'hourly': return `${timing.minute} * * * *`
    case 'daily': return `${minute} ${hour} * * *`
    case 'weekly': return `${minute} ${hour} * * ${timing.weekdays.join(',')}`
    case 'monthly': return `${minute} ${hour} ${timing.days} * *`
    case 'yearly': return `${minute} ${hour} ${timing.days} ${timing.months.join(',')} *`
    case 'advanced': return advanced
  }
}

function scheduleTiming(expression = '30 9 * * 1'): ScheduleTiming {
  const [minute, hour, days, months, weekdays] = expression.split(' ')
  const timing: ScheduleTiming = { frequency: 'advanced', time: '09:30', minute: '30', weekdays: [1], days: '1', months: [1] }
  if (!/^\d+$/.test(minute)) return timing
  timing.minute = minute
  if (hour === '*' && days === '*' && months === '*' && weekdays === '*') return { ...timing, frequency: 'hourly' }
  if (!/^\d+$/.test(hour)) return timing
  timing.time = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
  if (days === '*' && months === '*' && weekdays === '*') return { ...timing, frequency: 'daily' }
  if (days === '*' && months === '*' && /^\d+(,\d+)*$/.test(weekdays)) {
    return { ...timing, frequency: 'weekly', weekdays: weekdays.split(',').map(value => Number(value) % 7) }
  }
  if (/^\d+(,\d+)*$/.test(days) && weekdays === '*') {
    timing.days = days
    if (months === '*') return { ...timing, frequency: 'monthly' }
    if (/^\d+(,\d+)*$/.test(months)) return { ...timing, frequency: 'yearly', months: months.split(',').map(Number) }
  }
  return timing
}

const frequencyKeys = {
  hourly: 'scheduleHourly', daily: 'scheduleDaily', weekly: 'scheduleWeekly',
  monthly: 'scheduleMonthly', yearly: 'scheduleYearly', advanced: 'scheduleAdvanced',
} as const

export function SnapshotWorker({ onOpenReports }: { onOpenReports: (batchId?: string) => void }) {
  const { t, formatDate, errorMessage, language } = useLocale()
  const [data, setData] = useState<ReviewSchedulePage | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [actionError, setActionError] = useState<Error | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [draft, setDraft] = useState<ReviewScheduleInput | null>(null)
  const [editing, setEditing] = useState<ReviewSchedule | undefined>()
  const [timing, setTiming] = useState<ScheduleTiming>(() => scheduleTiming())
  const [preview, setPreview] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [removing, setRemoving] = useState<ReviewSchedule | null>(null)
  const expression = draft ? scheduleExpression(timing, draft.cron) : ''
  const timingValid = (timing.frequency !== 'weekly' || timing.weekdays.length > 0) &&
    (timing.frequency !== 'yearly' || timing.months.length > 0)

  useEffect(() => {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const load = async () => {
      try {
        const page = await fetchReviewSchedules(controller.signal)
        if (!controller.signal.aborted) { setData(page); setError(null) }
      } catch (cause) {
        if (!controller.signal.aborted) {
          console.error('Review schedules failed to load:', cause)
          setError(cause instanceof Error ? cause : new Error(String(cause)))
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(() => { void load() }, 10000)
      }
    }
    void load()
    return () => { controller.abort(); clearTimeout(timer) }
  }, [refresh])

  function edit(schedule?: ReviewSchedule) {
    const selection = schedule ?? { name: t('scheduleDefaultName'), enabled: false, cron: '30 9 * * 1', skip_holidays: true }
    setDraft(selection); setEditing(schedule); setTiming(scheduleTiming(selection.cron))
    setPreview([]); setActionError(null); setSaved(false)
  }

  async function act(action: () => Promise<void>) {
    setBusy(true); setActionError(null); setSaved(false)
    try { await action() } catch (cause) {
      console.error('Review schedule operation failed:', cause)
      setActionError(cause instanceof Error ? cause : new Error(String(cause)))
    } finally { setBusy(false) }
  }

  function changed() {
    setPreview([]); setActionError(null); setSaved(false)
  }

  function selection(value: ReviewScheduleInput): ReviewScheduleInput {
    return { name: value.name.trim(), enabled: value.enabled, skip_holidays: value.skip_holidays, cron: expression }
  }

  function submit(event: FormEvent, value: ReviewScheduleInput) {
    event.preventDefault()
    void act(async () => {
      await saveReviewSchedule(selection(value), editing)
      setDraft(null); setSaved(true); setRefresh(value => value + 1)
    })
  }

  const weekday = (day: number) => new Intl.DateTimeFormat(language, { weekday: 'short', timeZone: 'UTC' })
    .format(new Date(Date.UTC(2024, 0, 7 + day)))
  const month = (value: number) => new Intl.DateTimeFormat(language, { month: 'short', timeZone: 'UTC' })
    .format(new Date(Date.UTC(2024, value - 1, 1)))

  return <>
    <div className="page-heading">
      <div><p className="eyebrow">{t('operations')}</p><h1>{t('snapshotWorker')}</h1>
        <p className="page-description">{t('workerSchedule')}</p></div>
      <div className="configuration-actions">
        <button className="button secondary" onClick={() => onOpenReports()}><Icon name="camera" size={16} />{t('manualCaptureOpen')}</button>
        <button className="button secondary" onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" size={16} />{t('refresh')}</button>
      </div>
    </div>
    <section className="records-panel" aria-labelledby="schedule-heading">
      <div className="panel-heading"><h2 id="schedule-heading">{t('scheduleHeading')}</h2>
        <button className="button primary" disabled={busy || draft !== null} onClick={() => edit()}>{t('scheduleAdd')}</button></div>
      <div className="worker-panel-body">
        <p className="field-hint">{t('scheduleTimezone')}</p>
        {error && <p className="error-message" role="alert">{errorMessage(error)}</p>}
        {!data && !error && <p role="status">{t('loadingTitle')}</p>}
        {data && <>
          <p role="status" className={data.scheduler_online ? 'muted' : 'error-message'}>
            {t(data.scheduler_online ? 'scheduleOnline' : 'scheduleOffline')}</p>
          {!data.schedules.length && <p>{t('scheduleEmpty')}</p>}
          <div className="schedule-list">{data.schedules.map(schedule => <article className="schedule-card" key={schedule.id}>
            <div><h3>{schedule.name}</h3><span className="count-badge">{t(schedule.enabled ? 'aiAutoOn' : 'aiAutoOff')}</span></div>
            <code>{schedule.cron}</code>
            <p>{t('scheduleNext')}: {schedule.next_run ? formatDate(schedule.next_run) : t('schedulePaused')}</p>
            <p className="muted">{t(schedule.skip_holidays ? 'scheduleSkipHolidays' : 'scheduleIncludeHolidays')}</p>
            <div className="schedule-actions">
              <button className="button secondary compact" disabled={busy || draft !== null} onClick={() => edit(schedule)}>{t('scheduleEdit')}</button>
              <button className="button secondary compact" disabled={busy || draft !== null} onClick={() => {
                void act(async () => { await saveReviewSchedule({ ...schedule, enabled: !schedule.enabled }, schedule)
                  setSaved(true); setRefresh(value => value + 1) })
              }}>{t(schedule.enabled ? 'schedulePause' : 'scheduleResume')}</button>
              <button className="text-button" disabled={busy || draft !== null} onClick={() => setRemoving(schedule)}>{t('scheduleDelete')}</button>
            </div>
          </article>)}</div>
        </>}
        {actionError && <p className="error-message" role="alert">{errorMessage(actionError)}</p>}
        {saved && <p role="status">{t('scheduleSaved')}</p>}
        {draft && <form className="schedule-editor" onSubmit={event => submit(event, draft)} onChange={changed}>
          <h3>{t(editing ? 'scheduleEdit' : 'scheduleAdd')}</h3>
          <fieldset disabled={busy}>
            <div className="schedule-fields">
              <label>{t('scheduleName')}<input required maxLength={80} value={draft.name}
                onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
              <label>{t('scheduleFrequency')}<select value={timing.frequency} onChange={event => {
                const frequency = frequencies.find(value => value === event.target.value)
                if (frequency) {
                  if (frequency === 'advanced') setDraft({ ...draft, cron: expression })
                  setTiming({ ...timing, frequency })
                }
              }}>{frequencies.map(value => <option key={value} value={value}>{t(frequencyKeys[value])}</option>)}</select></label>
              {timing.frequency === 'hourly' ? <label>{t('scheduleMinute')}<input type="number" min={0} max={59} required value={timing.minute}
                onChange={event => setTiming({ ...timing, minute: event.target.value })} /></label> :
                timing.frequency !== 'advanced' && <label>{t('scheduleTime')}<input type="time" required value={timing.time}
                  onChange={event => setTiming({ ...timing, time: event.target.value })} /></label>}
              {(timing.frequency === 'monthly' || timing.frequency === 'yearly') && <label>{t('scheduleDays')}
                <input required pattern="[0-9]+(,[0-9]+)*" value={timing.days} maxLength={100}
                  onChange={event => setTiming({ ...timing, days: event.target.value })} />
                <span className="field-hint">{t('scheduleDaysHint')}</span></label>}
            </div>
            {timing.frequency === 'weekly' && <fieldset><legend>{t('scheduleWeekdays')}</legend>
              <div className="schedule-choices">{[1, 2, 3, 4, 5, 6, 0].map(day => <label className="report-option" key={day}>
                <input type="checkbox" checked={timing.weekdays.includes(day)} onChange={event =>
                  setTiming({ ...timing, weekdays: event.target.checked ? [...timing.weekdays, day].sort() : timing.weekdays.filter(value => value !== day) })} />
                {weekday(day)}</label>)}</div></fieldset>}
            {timing.frequency === 'yearly' && <fieldset><legend>{t('scheduleMonths')}</legend>
              <div className="schedule-choices">{Array.from({ length: 12 }, (_, index) => index + 1).map(value => <label className="report-option" key={value}>
                <input type="checkbox" checked={timing.months.includes(value)} onChange={event =>
                  setTiming({ ...timing, months: event.target.checked ? [...timing.months, value].sort((a, b) => a - b) : timing.months.filter(month => month !== value) })} />
                {month(value)}</label>)}</div></fieldset>}
            {timing.frequency === 'advanced' && <label>{t('scheduleCron')}<input required value={draft.cron} maxLength={160} spellCheck={false}
              onChange={event => setDraft({ ...draft, cron: event.target.value })} />
              <span className="field-hint">{t('scheduleCronHint')}</span></label>}
            {!timingValid && <p className="error-message">{t('scheduleSelectOne')}</p>}
            <label className="report-option"><input type="checkbox" checked={draft.skip_holidays}
              onChange={event => setDraft({ ...draft, skip_holidays: event.target.checked })} />{t('scheduleSkipHolidays')}</label>
            <label className="report-option"><input type="checkbox" checked={draft.enabled}
              onChange={event => setDraft({ ...draft, enabled: event.target.checked })} />{t('scheduleEnable')}</label>
            <p className="field-hint">{t('aiConsent')}</p>
            <div className="schedule-actions">
              <button className="button secondary" type="button" disabled={!timingValid} onClick={() => {
                void act(async () => { const result = await previewReviewSchedule(selection(draft)); setPreview(result.next_runs) })
              }}>{t('schedulePreview')}</button>
              <button className="button primary" type="submit" disabled={!timingValid}>{t('scheduleSave')}</button>
              <button className="text-button" type="button" onClick={() => { setDraft(null); setActionError(null) }}>{t('cancel')}</button>
            </div>
            {!!preview.length && <div role="status"><strong>{t('scheduleUpcoming')}</strong>
              <ol>{preview.map(date => <li key={date}><time dateTime={date}>{formatDate(date)}</time></li>)}</ol></div>}
          </fieldset>
        </form>}
        <details className="report-help"><summary>{t('schedulePolicyTitle')}</summary>
          <div className="report-help-content"><p>{t('schedulePolicy')}</p><p>{t('scheduleBackendHint')}</p></div>
        </details>
      </div>
    </section>
    <section className="records-panel" aria-labelledby="schedule-history">
      <div className="panel-heading"><h2 id="schedule-history">{t('scheduleHistory')}</h2></div>
      <div className="worker-panel-body"><p className="field-hint">{t('scheduleHistoryHint')}</p></div>
      <div className="table-scroll"><table className="schedule-history-table"><thead><tr>
        <th>{t('scheduleName')}</th><th>{t('schedulePlanned')}</th><th>{t('scheduleStatus')}</th><th>{t('actions')}</th>
      </tr></thead><tbody>
        {data?.history.map(row => <tr key={row.id}><td>{row.schedule_name}</td>
          <td><time dateTime={row.scheduled_at}>{formatDate(row.scheduled_at)}</time></td>
          <td>{t(inspectionStatusKeys[row.status])}{row.message && <p className="field-hint">{row.message}</p>}</td>
          <td>{row.has_report && <button className="text-button" onClick={() => onOpenReports(row.batch_id)}>{t('batchViewReport')}</button>}</td></tr>)}
        {data && !data.history.length && <tr><td colSpan={4}>{t('scheduleHistoryEmpty')}</td></tr>}
      </tbody></table></div>
    </section>
    {removing && <Dialog title={t('scheduleDelete')} subtitle={removing.name} onClose={() => { if (!busy) setRemoving(null) }}>
      <div className="dialog-body" id="dialog-description"><p>{t('scheduleDeleteHint')}</p>
        {actionError && <p className="error-message" role="alert">{errorMessage(actionError)}</p>}
        <button className="button primary" disabled={busy} onClick={() => {
          void act(async () => { await deleteReviewSchedule(removing); setRemoving(null); setSaved(true); setRefresh(value => value + 1) })
        }}>{t('scheduleDelete')}</button></div>
    </Dialog>}
  </>
}
