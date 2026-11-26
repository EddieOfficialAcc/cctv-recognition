import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('record drawer localizes new timestamp details and preserves old explanations', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { RecordDrawer } = await server.ssrLoadModule('/src/components/RecordDrawer.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    const oldStorage = globalThis.localStorage
    globalThis.localStorage = { getItem: () => 'zh-Hant' }
    try {
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const base = {
        id: '00000000-0000-0000-0000-000000000001', camera_name: 'CH01', shop_code: 'SHOP',
        region: 'Hong Kong', filename: 'camera.jpg', width: 100, height: 100,
        uploaded_at: '2026-11-24T02:30:00Z', captured_at: '2026-11-24T02:30:00Z',
        outcome: 'flagged', review_status: 'new', review_note: '', reviewed_at: null,
      }
      function render(analyzer_version, explanation, metadata = {}) {
        const record = { ...base, ...metadata, analyzer_version, checks: [{ category: 'timestamp', status: 'flagged', explanation }] }
        return renderToStaticMarkup(React.createElement(Provider, null,
          React.createElement(RecordDrawer, { record, onClose() {}, onSaved() {} })))
      }
      const current = render('vision-llm-v2', JSON.stringify({
        state: 'readable', reading: '2026-11-24 10:25:00',
        difference_seconds: -300, threshold_seconds: 180, reason: 'outside',
      }))
      assert.match(current, /攝影機時間超出允許偏差/)
      assert.match(current, /2026-11-24 10:25:00/)
      assert.match(current, /-300 秒/)
      assert.match(current, /180 秒/)
      assert.match(current, /<dt>時間戳<\/dt>/)
      assert.doesNotMatch(current, /右上角|左上角/)
      const missing = render('vision-llm-v2', JSON.stringify({
        state: 'missing', reading: null, difference_seconds: null, threshold_seconds: 180, reason: 'missing',
      }), { shop_code: '', camera_name: '' })
      assert.match(missing, /AI 在檢查範圍內未找到時間戳/)
      assert.match(missing, /未提供/)
      assert.match(missing, /上傳時這兩個欄位均為選填/)
      assert.match(missing, /disabled="">載入目前基準畫面/)
      assert.match(missing, /disabled="">批准此截圖為基準畫面/)
      const old = render('vision-llm-v1', 'Camera timestamp differs from the supplied capture time by approximately 300 seconds.')
      assert.match(old, /攝影機時間與提供的擷取時間相差約 300 秒/)
      assert.doesNotMatch(old, /AI 讀數/)
    } finally {
      if (oldStorage === undefined) delete globalThis.localStorage
      else globalThis.localStorage = oldStorage
    }
  } finally {
    await server.close()
  }
})
