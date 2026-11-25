import assert from 'node:assert/strict'
import test from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

test('upload marks shop and camera optional and explains upper-right timestamps in all locales', async () => {
  const server = await createServer({ server: { middlewareMode: true, hmr: false }, appType: 'custom' })
  const oldStorage = globalThis.localStorage
  try {
    const { UploadDialog } = await server.ssrLoadModule('/src/components/UploadDialog.tsx')
    const { LocaleContext, useLanguageState } = await server.ssrLoadModule('/src/localization.ts')
    for (const [language, corner] of [['en', 'upper-right'], ['zh-Hant', '右上角'], ['zh-Hans', '右上角']]) {
      globalThis.localStorage = { getItem: () => language }
      function Provider({ children }) {
        return React.createElement(LocaleContext.Provider, { value: useLanguageState() }, children)
      }
      const html = renderToStaticMarkup(React.createElement(Provider, null,
        React.createElement(UploadDialog, { onClose() {}, onUploaded() {} })))
      for (const name of ['shop_code', 'camera_name']) {
        assert.match(html, new RegExp(`<span class="optional">[^<]+</span><input[^>]*name="${name}"`))
        const input = html.match(new RegExp(`<input[^>]*name="${name}"[^>]*>`))?.[0]
        assert.ok(input)
        assert.doesNotMatch(input, /required|pattern=/)
      }
      assert.ok(html.includes(corner))
      assert.doesNotMatch(html, /upper-left|左上角/)
    }
  } finally {
    if (oldStorage === undefined) delete globalThis.localStorage
    else globalThis.localStorage = oldStorage
    await server.close()
  }
})
