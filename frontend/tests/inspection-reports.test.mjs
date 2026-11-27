import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('automatic schedule page locales and revision-aware API contracts', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  const oldFetch = globalThis.fetch
  try {
    const api = await server.ssrLoadModule('/src/api.ts')
    const { SnapshotWorker } = await server.ssrLoadModule('/src/components/SnapshotWorker.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, heading] of [
      ['en', 'Automatic AI checks'], ['zh-Hant', '自動 AI 檢查'], ['zh-Hans', '自动 AI 检查'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null, React.createElement(SnapshotWorker, { onOpenReports() {} })))
      assert.ok(html.includes(heading))
      assert.match(html, /UTC\+08:00/)
      assert.match(html, /<details class="report-help"><summary>[^<]+<\/summary><div class="report-help-content"><p>/)
      assert.doesNotMatch(html, /SCHEDULED_INSPECTION_ENABLED/)
    }
    const input = { name: 'Weekly', enabled: false, cron: '30 9 * * 1', skip_holidays: true }
    const schedule = { ...input, id: 'schedule', revision: 3, next_run: null }
    const dates = ['2026-11-30T01:30:00Z', '2026-12-07T01:30:00Z', '2026-12-14T01:30:00Z']
    const page = { schedules: [schedule], history: [], scheduler_online: false, timezone: 'Asia/Hong_Kong' }
    const calls = []
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json(options?.method === 'DELETE' ? { deleted: true } :
        url.endsWith('/preview') ? { next_runs: dates } : options?.method ? schedule : page)
    }
    const signal = new AbortController().signal
    await api.fetchReviewSchedules(signal)
    await api.saveReviewSchedule(input)
    await api.saveReviewSchedule(schedule, schedule)
    await api.previewReviewSchedule(input)
    await api.deleteReviewSchedule(schedule)
    assert.equal(calls[0].options.cache, 'no-store')
    assert.equal(calls[0].options.signal, signal)
    assert.deepEqual(JSON.parse(calls[1].options.body), input)
    assert.equal(calls[1].options.method, 'POST')
    assert.equal(calls[2].url, '/api/review-schedules/schedule')
    assert.equal(calls[2].options.method, 'PUT')
    assert.deepEqual(JSON.parse(calls[2].options.body), { ...input, revision: 3 })
    assert.equal(calls[3].url, '/api/review-schedules/preview')
    assert.equal(calls[4].url, '/api/review-schedules/schedule?revision=3')
    for (const call of calls.slice(1)) assert.equal(call.options.headers['X-Requested-With'], 'XMLHttpRequest')
    globalThis.fetch = async () => Response.json({ ...schedule, enabled: true })
    await assert.rejects(api.saveReviewSchedule(input), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ next_runs: ['invalid date'] })
    await assert.rejects(api.previewReviewSchedule(input), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ ...page, timezone: 'UTC' })
    await assert.rejects(api.fetchReviewSchedules(signal), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ detail: 'Schedule changed.' }, { status: 409 })
    await assert.rejects(api.saveReviewSchedule(input, schedule), { code: 'scheduleConflictError' })
    globalThis.fetch = async () => Response.json({ detail: [] }, { status: 422 })
    await assert.rejects(api.previewReviewSchedule(input), { code: 'scheduleValidationError' })
  } finally {
    globalThis.fetch = oldFetch
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})

test('AI review progress, localization, queue API contracts and strict response validation', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  const oldFetch = globalThis.fetch
  try {
    const api = await server.ssrLoadModule('/src/api.ts')
    const { AnalysisProgress, SnapshotAnalysis } = await server.ssrLoadModule('/src/components/SnapshotAnalysis.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    const summary = { total: 20, queued: 10, analyzing: 2, completed: 6, failed: 2, stopped: 0, flagged: 3,
      worker_online: false, auto_analyze: false, stop_requested: false }
    for (const [language, heading, auto] of [
      ['en', 'Snapshot AI review', 'Automatically analyze all snapshots in future manual batches'],
      ['zh-Hant', '快照 AI 審查', '自動分析日後手動批次的所有快照'],
      ['zh-Hans', '快照 AI 审查', '自动分析以后手动批次的所有快照'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const progress = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(AnalysisProgress, { summary, saved: 1402 })))
      assert.match(progress, /<progress max="20" value="8"/)
      assert.match(progress, /1402/)
      assert.match(progress, /role="alert"/)
      assert.match(progress, /class="analysis-metrics"/)
      assert.match(progress, /<dd>6<\/dd>/)
      assert.match(progress, /<dd>2<\/dd>/)
      const controls = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(SnapshotAnalysis, { manual: true, onSelect() {} })))
      assert.ok(controls.includes(heading))
      assert.ok(controls.includes(auto))
      assert.match(controls, /SNAPSHOT_ANALYSIS_CONCURRENCY/)
      assert.match(controls, /<details class="report-help analysis-automation">/)
      assert.match(controls, /aria-describedby="analysis-auto-hint"/)
      assert.doesNotMatch(controls, /<details[^>]*\sopen/)
    }
    const settings = { auto_analyze: false, configured: true, worker_enabled: true, worker_online: true, concurrency: 2 }
    const body = { summary, total: 20, page: 1, page_size: 20, items: [
      { id: 'item', shop_code: '001', region: 'Hong Kong', channel: 1, status: 'queued',
        attempts: 0, message: '', retry_at: null, record: null },
    ] }
    const calls = []
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json(url.includes('/settings') ? settings : body)
    }
    const signal = new AbortController().signal
    await api.startBatchAnalysis('batch', 20)
    await api.startBatchAnalysis('batch', null)
    await api.retryAnalysis('batch', true)
    await api.setAutoAnalysis(true)
    await api.fetchAnalysis('2026-11-26', false, 2, 'failed', signal)
    assert.equal(calls[0].url, '/api/snapshot-batches/batch/analysis')
    assert.deepEqual(JSON.parse(calls[0].options.body), { limit: 20 })
    assert.deepEqual(JSON.parse(calls[1].options.body), { limit: null })
    assert.equal(calls[2].url, '/api/snapshot-analysis/batch/batch/retry')
    assert.deepEqual(JSON.parse(calls[3].options.body), { auto_analyze: true })
    assert.equal(calls[3].options.method, 'PATCH')
    for (const call of calls.slice(0, 4)) assert.equal(call.options.headers['X-Requested-With'], 'XMLHttpRequest')
    assert.equal(calls[4].url, '/api/snapshot-analysis/scheduled/2026-11-26?page=2&page_size=20&status=failed')
    assert.equal(calls[4].options.signal, signal)
    assert.equal(calls[4].options.cache, 'no-store')
    await api.setAnalysisStopped('batch', true, true)
    await api.setAnalysisStopped('batch', true, false)
    assert.equal(calls[5].url, '/api/snapshot-analysis/batch/batch/stop')
    assert.equal(calls[6].url, '/api/snapshot-analysis/batch/batch/resume')
    assert.equal(calls[5].options.headers['X-Requested-With'], 'XMLHttpRequest')
    globalThis.fetch = async () => Response.json({ ...body, items: [{ ...body.items[0], status: 'completed' }] })
    await assert.rejects(api.startBatchAnalysis('batch', 20), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ detail: 'Vision analysis is not configured.' }, { status: 503 })
    await assert.rejects(api.startBatchAnalysis('batch', 20), { code: 'aiConfigurationError' })
    globalThis.fetch = async () => Response.json({ detail: 'AI review storage or configuration unavailable.' }, { status: 503 })
    await assert.rejects(api.retryAnalysis('batch', true), { code: 'aiStorageError' })
  } finally {
    globalThis.fetch = oldFetch
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})

test('inspection reports exposes bulk capture and run selection in all locales', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  const oldFetch = globalThis.fetch
  try {
    const { InspectionReports, InspectionProgress } = await server.ssrLoadModule('/src/components/InspectionReports.tsx')
    const { SnapshotBatchReportDetails } = await server.ssrLoadModule('/src/components/SnapshotBatchReportDialog.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, capture, schedule, results] of [
      ['en', 'Start manual capture', 'Automatic AI checks', 'Review a check'],
      ['zh-Hant', '啟動手動擷取', '自動 AI 檢查', '查看檢查結果'],
      ['zh-Hans', '启动手动抓取', '自动 AI 检查', '查看检查结果'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(InspectionReports, { onSelect() {}, onSchedules() {}, initialTab: 'capture' })))
      assert.ok(html.includes(capture))
      assert.match(html, /option value="manual" selected=""/)
      assert.match(html, /camera-snapshots\/batches/)
      assert.match(html, /role="tablist"/)
      assert.ok(html.indexOf('id="inspection-tab-capture"') < html.indexOf('id="inspection-tab-analysis"'))
      assert.match(html, /id="inspection-tab-capture"[^>]*aria-selected="true"/)
      assert.match(html, /id="inspection-tab-analysis"[^>]*aria-selected="false"/)
      assert.doesNotMatch(html, /type="checkbox"/)
      const defaultView = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(InspectionReports, { onSelect() {}, onSchedules() {} })))
      assert.ok(defaultView.indexOf('id="inspection-tab-capture"') < defaultView.indexOf('id="inspection-tab-analysis"'))
      assert.match(defaultView, /id="inspection-tab-analysis"[^>]*aria-selected="true"/)
      assert.match(defaultView, /id="inspection-tab-capture"[^>]*aria-selected="false"/)
      assert.match(defaultView, /option value="manual" selected=""/)
      assert.ok(defaultView.includes(capture), 'manual capture is available on the default view')
      assert.ok(defaultView.includes(schedule))
      assert.ok(defaultView.includes(results))
      assert.match(defaultView, /Dahua/)
      assert.match(defaultView, /Hikvision/)
      const progress = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(InspectionProgress, {
          manual: true,
          onViewReport() {},
          run: {
            id: 'test-batch', run_date: '2026-11-26', status: 'interrupted',
            started_at: null, finished_at: null,
            total: 1616, completed: 0, failed: 27, skipped: 12, interrupted: 1577, saved: 0,
            error: 'Local archive/report operation failed (PermissionError, Windows error 32).',
          },
        })))
      assert.match(progress, /<progress max="1616" value="39"/)
      assert.match(progress, /role="alert"/)
      assert.ok(progress.includes('Windows error 32'))
      assert.match(progress, /snapshot-batches\/test-batch\/report/)
      const viewLabels = { en: 'View report', 'zh-Hant': '檢視報告', 'zh-Hans': '查看报告' }
      assert.ok(progress.includes(viewLabels[language]))
      assert.match(progress, /download="snapshot-batch-test-batch.json"/)
      const activeProgress = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(InspectionProgress, {
          manual: true, onStop() {}, run: {
            id: 'test-batch', run_date: '2026-11-26', status: 'running',
            started_at: null, finished_at: null, total: 2, completed: 0, failed: 0, skipped: 0,
            interrupted: 0, saved: 0, stopped: 0, capture_brands: ['DAHAU', 'HIKVISION'],
            stop_requested: ['DAHAU'],
          },
        })))
      assert.match(activeProgress, /disabled=""/)
      assert.match(activeProgress, /Dahua/)
      assert.match(activeProgress, /Hikvision/)
      const details = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(SnapshotBatchReportDetails, { report: {
          id: 'test-batch', status: 'completed', started_at: '2026-11-26T06:00:00Z',
          finished_at: '2026-11-26T06:01:00Z', items: [
            { id: 'success', shop_code: '172', region: 'Hong Kong', channel: 1, camera_name: 'Channel 1',
              status: 'completed', coverage_method: 'configured_count', message: '',
              captured_at: '2026-11-26T06:00:10Z', has_evidence: true, has_record: false },
            { id: 'failure', shop_code: '173', region: 'Hong Kong', channel: 2, camera_name: 'Channel 2',
              status: 'failed', coverage_method: 'configured_count', message: 'Recorder returned HTTP 404.',
              captured_at: null, has_evidence: false, has_record: false },
          ],
        } })))
      assert.match(details, /<td>172<\/td>/)
      assert.match(details, /<td>173<\/td>/)
      assert.match(details, /Recorder returned HTTP 404\./)
      assert.match(details, /class="snapshot-report-overview"/)
      assert.match(details, /class="snapshot-report-metadata"/)
      assert.match(details, /option value="failed"/)
      assert.match(details, /option value="completed"/)
      assert.match(details, /option value="stopped"/)
    }
    const api = await server.ssrLoadModule('/src/api.ts')
    const calls = []
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json(url.includes('/exclusion') ? { skip: true } : {
        id: 'new-batch', run_date: '2026-11-26', status: 'running', started_at: null, finished_at: null,
        total: 3, completed: 0, failed: 0, skipped: 0, interrupted: 0, saved: 0,
      })

    }
    await api.startSnapshotBatch(true)
    await api.startSnapshotBatch(false, 'HIKVISION')
    await api.startSnapshotBatch(false, 'DAHAU')
    await api.setSnapshotChannelExcluded('previous-batch', 'channel-item', true)
    assert.equal(calls[0].url, '/api/snapshot-batches?recheck_skipped=true')
    assert.equal(calls[1].url, '/api/snapshot-batches?recheck_skipped=false&brand=HIKVISION')
    assert.equal(calls[2].url, '/api/snapshot-batches?recheck_skipped=false&brand=DAHAU')
    assert.equal(calls[3].url, '/api/snapshot-batches/previous-batch/items/channel-item/exclusion')
    assert.equal(calls[3].options.method, 'PATCH')
    assert.equal(calls[3].options.headers['X-Requested-With'], 'XMLHttpRequest')
    assert.deepEqual(JSON.parse(calls[3].options.body), { skip: true })
    await api.stopSnapshotBatchBrand('test-batch', 'HIKVISION')
    assert.equal(calls[4].url, '/api/snapshot-batches/test-batch/stop/HIKVISION')
    const report = {
      id: 'test-batch', status: 'completed', started_at: '2026-11-26T06:00:00Z', finished_at: null, items: [],
    }
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json(report, { headers: { 'Content-Disposition': 'attachment; filename="report.json"' } })
    }
    const signal = new AbortController().signal
    assert.deepEqual(await api.fetchSnapshotBatchReport('test-batch', signal), report)
    assert.equal(calls[5].url, '/api/snapshot-batches/test-batch/report')
    assert.equal(calls[5].options.signal, signal)
    assert.equal(calls[5].options.cache, 'no-store')
    globalThis.fetch = async () => Response.json({ ...report, id: 'wrong-batch' })
    await assert.rejects(api.fetchSnapshotBatchReport('test-batch', signal), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ ...report, items: [{ id: 'incomplete-channel' }] })
    await assert.rejects(api.fetchSnapshotBatchReport('test-batch', signal), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ detail: 'Storage unavailable.' }, { status: 503 })
    await assert.rejects(api.fetchSnapshotBatchReport('test-batch', signal), { code: 'storageError' })
    globalThis.fetch = async () => Response.json({ detail: 'Snapshot batch storage unavailable.' }, { status: 503 })
    await assert.rejects(api.fetchInspectionItems('test-batch', 1, signal, true), { code: 'batchStorageError' })
    globalThis.fetch = async () => Response.json({ detail: 'No supported recorders are configured for batch capture.' }, { status: 422 })
    await assert.rejects(api.startSnapshotBatch(false), { code: 'batchNoRecorders' })
    globalThis.fetch = async () => Response.json({ detail: 'No recorders match the selected brand for batch capture.' }, { status: 422 })
    await assert.rejects(api.startSnapshotBatch(false, 'HIKVISION'), { code: 'batchNoRecorders' })
  } finally {
    globalThis.fetch = oldFetch
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
