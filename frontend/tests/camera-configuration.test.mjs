import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('camera configuration sends combined filters and omits empty filters', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldFetch = globalThis.fetch
  try {
    const api = await server.ssrLoadModule('/src/api.ts')
    const calls = []
    globalThis.fetch = async url => {
      calls.push(url)
      return Response.json({ rows: [], total: 0, page: 1, page_size: 20 })
    }
    const signal = new AbortController().signal
    await api.fetchCameraConfiguration(2, signal, 'DAHAU', 'Macau', 'BestMart360')
    await api.fetchCameraConfiguration(1, signal, '', '', '')
    assert.equal(calls[0], '/api/camera-configuration?page=2&page_size=20&brand=DAHAU&region=Macau&company=BestMart360')
    assert.equal(calls[1], '/api/camera-configuration?page=1&page_size=20')
  } finally {
    globalThis.fetch = oldFetch
    await server.close()
  }
})

test('camera configuration exposes localized region and company filters', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  try {
    const { CameraConfiguration } = await server.ssrLoadModule('/src/components/CameraConfiguration.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, regionLabel, companyLabel] of [
      ['en', 'Filter by region', 'Filter by company'],
      ['zh-Hant', '按地區篩選', '按公司篩選'],
      ['zh-Hans', '按地区筛选', '按公司筛选'],
    ]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null, React.createElement(CameraConfiguration)))
      assert.ok(html.includes(regionLabel))
      assert.ok(html.includes(companyLabel))
      for (const value of ['Hong Kong', 'Macau', 'BestMart360', 'FoodVilla']) {
        assert.ok(html.includes(`value="${value}"`))
      }
    }
  } finally {
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
