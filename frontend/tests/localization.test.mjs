import assert from 'node:assert/strict'
import test from 'node:test'
import { isLanguage, messages, translate } from '../src/messages.ts'
import { parseTimestampDetails } from '../src/timestamp.ts'

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

test('timestamp details preserve signed differences and reject invalid stored data', () => {
  const details = {
    state: 'readable', reading: '2026-11-24 10:25:00',
    difference_seconds: -300, threshold_seconds: 180, reason: 'outside',
  }
  assert.deepEqual(parseTimestampDetails(JSON.stringify(details)), details)
  const compared = { ...details, model_comparison: 'outside' }
  assert.deepEqual(parseTimestampDetails(JSON.stringify(compared)), compared)
  assert.equal(parseTimestampDetails(JSON.stringify({ ...details, model_comparison: 'unknown' })), null)
  assert.equal(parseTimestampDetails(JSON.stringify({ ...details, model_comparison: null })), null)
  assert.equal(parseTimestampDetails('Camera timestamp differs from the supplied capture time by approximately 300 seconds.'), null)
  assert.equal(parseTimestampDetails(JSON.stringify({ ...details, threshold_seconds: -1 })), null)
  assert.equal(parseTimestampDetails(JSON.stringify({ ...details, reason: 'unknown' })), null)
  assert.equal(parseTimestampDetails(JSON.stringify({ ...details, difference_seconds: '300' })), null)
})
