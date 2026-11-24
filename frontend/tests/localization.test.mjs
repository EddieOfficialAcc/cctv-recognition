import assert from 'node:assert/strict'
import test from 'node:test'
import { isLanguage, messages, translate } from '../src/messages.ts'

test('all translations interpolate the same parameters without leaving tokens', () => {
  for (const [key, english] of Object.entries(messages.en)) {
    const names = [...english.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()
    const parameters = Object.fromEntries(names.map(name => [name, `測試-${name}`]))
    for (const language of ['en', 'zh-Hant', 'zh-Hans']) {
      const translatedNames = [...messages[language][key].matchAll(/\{(\w+)\}/g)]
        .map(match => match[1]).sort()
      assert.deepEqual(translatedNames, names, `${language}.${key}`)
      const result = translate(language, key, parameters)
      assert.ok(!/\{\w+\}/.test(result), `${language}.${key} has unresolved parameters`)
      for (const value of Object.values(parameters)) assert.ok(result.includes(value))
    }
  }
})

test('missing parameters are explicit errors rather than broken UI text', () => {
  assert.throws(() => translate('zh-Hant', 'uploadedNotice', { shop: '店舖' }), /camera/)
})

test('saved language values are restricted to the three supported locales', () => {
  assert.ok(isLanguage('zh-Hant'))
  assert.ok(isLanguage('zh-Hans'))
  assert.ok(isLanguage('en'))
  for (const value of [null, '', 'zh', 'en-US', '__proto__']) assert.equal(isLanguage(value), false)
})
