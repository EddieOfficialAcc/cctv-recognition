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
      assert.doesNotMatch(current, /LLM 時間比較/)
      const compared = render('vision-llm-v2', JSON.stringify({
        state: 'readable', reading: '2026-11-24 10:25:00',
        difference_seconds: -300, threshold_seconds: 180, reason: 'outside', model_comparison: 'outside',
      }))
      assert.match(compared, /<dt>LLM 時間比較<\/dt><dd>超出允許偏差<\/dd>/)
      const disagreement = render('vision-llm-v2', JSON.stringify({
        state: 'readable', reading: '2026-11-24 10:25:00', difference_seconds: -300,
        threshold_seconds: 180, reason: 'comparison_disagreement', model_comparison: 'within',
      }))
      assert.match(disagreement, /LLM 的比較結果與經驗證的時間差不一致/)
      assert.match(disagreement, /<dt>LLM 時間比較<\/dt><dd>在允許偏差範圍內<\/dd>/)
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
      const daily = render('vision-llm-daily-v1', JSON.stringify({
        state: 'readable', reading: '2026-11-24 10:25:00',
        difference_seconds: -300, threshold_seconds: 180, reason: 'outside',
      }))
      assert.match(daily, /攝影機時間超出允許偏差/)
      assert.match(daily, /目前擷取影像/)
      assert.match(daily, /正在載入前一天比較影像/)
      assert.match(daily, /載入目前基準畫面/)
      assert.match(daily, /自動檢查會改用前一天的快照/)
      assert.doesNotMatch(old, /前一天比較影像/)
      assert.match(old, /載入目前基準畫面/)
    } finally {
      if (oldStorage === undefined) delete globalThis.localStorage
      else globalThis.localStorage = oldStorage
    }
  } finally {
    await server.close()
  }
})

test('daily comparison API validates metadata and distinguishes missing from errors', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldFetch = globalThis.fetch
  try {
    const api = await server.ssrLoadModule('/src/api.ts')
    const signal = new AbortController().signal
    let call
    globalThis.fetch = async (url, options) => {
      call = { url, options }
      return Response.json({ captured_at: '2026-11-23T02:30:45Z' })
    }
    assert.deepEqual(await api.fetchComparison('record/1', signal), { captured_at: '2026-11-23T02:30:45Z' })
    assert.equal(call.url, '/api/recognitions/record%2F1/comparison')
    assert.equal(call.options.signal, signal)
    assert.equal(call.options.cache, 'no-store')
    assert.equal(api.comparisonEvidenceUrl('record/1'), '/api/recognitions/record%2F1/comparison/evidence')
    for (const captured_at of [null, '', 'not-a-date']) {
      globalThis.fetch = async () => Response.json({ captured_at })
      await assert.rejects(api.fetchComparison('id', signal), { code: 'responseError' })
    }
    for (const [status, code] of [[404, 'notFoundError'], [500, 'requestError']]) {
      globalThis.fetch = async () => new Response(null, { status })
      await assert.rejects(api.fetchComparison('id', signal), { code })
    }
  } finally {
    globalThis.fetch = oldFetch
    await server.close()
  }
})

test('daily comparison states and explanations are localized in all languages', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  try {
    const { DailyComparisonView } = await server.ssrLoadModule('/src/components/DailyComparison.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, heading, unavailable, error, scene] of [
      ['en', 'Previous-day comparison', 'No previous-day comparison', 'could not be loaded', 'Previous-day snapshot unavailable'],
      ['zh-Hant', '前一天比較影像', '此紀錄沒有可用的前一天比較影像', '無法載入前一天比較資料', '沒有前一天的快照'],
      ['zh-Hans', '前一天比较图像', '此记录没有可用的前一天比较图像', '无法加载前一天比较信息', '没有前一天的快照'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      let locale
      function Provider({ children }) {
        locale = useLanguageState()
        return React.createElement(LocaleContext.Provider, { value: locale }, children)
      }
      const render = state => renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(DailyComparisonView, { recordId: 'abc', state })))
      assert.ok(render({ status: 'loading' }).includes(heading))
      assert.ok(render({ status: 'unavailable' }).includes(unavailable))
      assert.ok(render({ status: 'error' }).includes(error))
      const available = render({ status: 'available', comparison: { captured_at: '2026-11-23T02:30:45Z' } })
      assert.match(available, /\/api\/recognitions\/abc\/comparison\/evidence/)
      assert.match(available, /10:30:45/)
      const record = { analyzer_version: 'vision-llm-daily-v1' }
      assert.ok(locale.checkExplanation(record, {
        category: 'scene', explanation: 'Previous-day snapshot unavailable. Camera movement was not assessed.',
      }).includes(scene))
      const localized = JSON.stringify({ en: 'English', zh_hant: '繁體', zh_hans: '简体' })
      assert.equal(locale.checkExplanation(record, { category: 'scene', explanation: localized }),
        { en: 'English', 'zh-Hant': '繁體', 'zh-Hans': '简体' }[language])
    }
  } finally {
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
