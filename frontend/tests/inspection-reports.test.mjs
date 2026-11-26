import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('inspection reports exposes bulk capture and run selection in all locales', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  try {
    const { InspectionReports, InspectionProgress } = await server.ssrLoadModule('/src/components/InspectionReports.tsx')
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
    }
  } finally {
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
