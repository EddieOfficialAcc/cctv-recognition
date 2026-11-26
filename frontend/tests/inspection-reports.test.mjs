import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('inspection reports exposes bulk capture and run selection in all locales', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  const oldFetch = globalThis.fetch
  try {
    const { InspectionReports, InspectionProgress } = await server.ssrLoadModule('/src/components/InspectionReports.tsx')
    const { SnapshotBatchReportDetails } = await server.ssrLoadModule('/src/components/SnapshotBatchReportDialog.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, capture] of [
      ['en', 'Capture all DAHAU cameras'], ['zh-Hant', '擷取所有 DAHAU 攝影機'], ['zh-Hans', '抓取所有 DAHAU 摄像机'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(InspectionReports, { onSelect() {} })))
      assert.ok(html.includes(capture))
      assert.match(html, /option value="manual"/)
      assert.match(html, /camera-snapshots\/batches/)
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
      assert.match(details, /option value="failed"/)
      assert.match(details, /option value="completed"/)
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
    await api.setSnapshotChannelExcluded('previous-batch', 'channel-item', true)
    assert.equal(calls[0].url, '/api/snapshot-batches?recheck_skipped=true')
    assert.equal(calls[1].url, '/api/snapshot-batches/previous-batch/items/channel-item/exclusion')
    assert.equal(calls[1].options.method, 'PATCH')
    assert.equal(calls[1].options.headers['X-Requested-With'], 'XMLHttpRequest')
    assert.deepEqual(JSON.parse(calls[1].options.body), { skip: true })
    const report = {
      id: 'test-batch', status: 'completed', started_at: '2026-11-26T06:00:00Z', finished_at: null, items: [],
    }
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json(report, { headers: { 'Content-Disposition': 'attachment; filename="report.json"' } })
    }
    const signal = new AbortController().signal
    assert.deepEqual(await api.fetchSnapshotBatchReport('test-batch', signal), report)
    assert.equal(calls[2].url, '/api/snapshot-batches/test-batch/report')
    assert.equal(calls[2].options.signal, signal)
    assert.equal(calls[2].options.cache, 'no-store')
    globalThis.fetch = async () => Response.json({ ...report, id: 'wrong-batch' })
    await assert.rejects(api.fetchSnapshotBatchReport('test-batch', signal), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ ...report, items: [{ id: 'incomplete-channel' }] })
    await assert.rejects(api.fetchSnapshotBatchReport('test-batch', signal), { code: 'responseError' })
    globalThis.fetch = async () => Response.json({ detail: 'Storage unavailable.' }, { status: 503 })
    await assert.rejects(api.fetchSnapshotBatchReport('test-batch', signal), { code: 'storageError' })
  } finally {
    globalThis.fetch = oldFetch
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
