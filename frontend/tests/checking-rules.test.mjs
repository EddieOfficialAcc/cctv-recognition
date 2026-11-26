import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('checking rules API preserves revision and criteria, and rejects invalid responses', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldFetch = globalThis.fetch
  try {
    const api = await server.ssrLoadModule('/src/api.ts')
    const values = {
      signal_instructions: 'Flag no-signal overlays.',
      scene_instructions: '留意出口被遮擋。',
      timestamp_position: 'lower-left', timestamp_drift_seconds: 600,
    }
    const rules = { ...values, revision: 3, updated_at: '2026-11-26T09:30:00Z' }
    const page = { defaults: { ...values, timestamp_drift_seconds: 180 }, rules }
    const calls = []
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options })
      return Response.json(page)
    }
    const signal = new AbortController().signal
    assert.deepEqual(await api.fetchCheckingRules(signal), page)
    await api.saveCheckingRules(values, 2)
    assert.equal(calls[0].url, '/api/checking-rules')
    assert.equal(calls[0].options.cache, 'no-store')
    assert.equal(calls[0].options.signal, signal)
    assert.equal(calls[1].options.method, 'PUT')
    assert.equal(calls[1].options.headers['X-Requested-With'], 'XMLHttpRequest')
    assert.deepEqual(JSON.parse(calls[1].options.body), { ...values, revision: 2 })
    for (const invalid of [
      { timestamp_drift_seconds: 0 }, { timestamp_drift_seconds: 86401 }, { timestamp_drift_seconds: 1.5 },
      { timestamp_position: 'center' }, { signal_instructions: ' ' }, { scene_instructions: 'x'.repeat(1501) },
      { revision: -1 }, { updated_at: 'invalid' },
    ]) {
      globalThis.fetch = async () => Response.json({ ...page, rules: { ...rules, ...invalid } })
      await assert.rejects(api.fetchCheckingRules(signal), { code: 'responseError' })
    }
    for (const [status, code] of [[409, 'rulesConflictError'], [422, 'rulesValidationError'], [503, 'rulesStorageError']]) {
      globalThis.fetch = async () => Response.json({ detail: 'Rule operation failed.' }, { status })
      await assert.rejects(api.saveCheckingRules(values, 2), { code })
    }
  } finally {
    globalThis.fetch = oldFetch
    await server.close()
  }
})

test('checking rules workspace has localized scope and loading state in every language', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  try {
    const { CheckingRules } = await server.ssrLoadModule('/src/components/CheckingRules.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, heading, scope] of [
      ['en', 'Checking rules', 'One shared rule set for all cameras'],
      ['zh-Hant', '檢查規則', '所有攝影機共用同一套規則'],
      ['zh-Hans', '检查规则', '所有摄像机共用同一套规则'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null, React.createElement(CheckingRules)))
      assert.ok(html.includes(`<h1>${heading}</h1>`))
      assert.ok(html.includes(scope))
      assert.match(html, /role="status"/)
      assert.doesNotMatch(html, /<textarea/)
    }
  } finally {
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
