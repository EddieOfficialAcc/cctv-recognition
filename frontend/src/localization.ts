import { createContext, useContext, useEffect, useState } from 'react'
import { ApiError, type RecognitionCheck, type RecognitionRecord } from './api'
import { isLanguage, translate, type Language, type MessageKey } from './messages'

const STORAGE_KEY = 'cctv-inspection-language'
const dateLocales = { en: 'en-HK', 'zh-Hant': 'zh-HK', 'zh-Hans': 'zh-CN' } as const

function readPreference(): { language: Language; storageError: boolean } {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved !== null && !isLanguage(saved)) {
      console.warn('Unsupported stored interface language:', saved)
      return { language: 'en', storageError: true }
    }
    return { language: saved ?? 'en', storageError: false }
  } catch (error) {
    if (!(error instanceof DOMException)) throw error
    console.error('Language preference could not be restored:', error)
    return { language: 'en', storageError: true }
  }
}

export function useLanguageState() {
  const [preference, setPreference] = useState(readPreference)
  const { language, storageError } = preference

  useEffect(() => {
    document.documentElement.lang = language
  }, [language])

  function setLanguage(next: Language) {
    let failed = false
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch (error) {
      if (!(error instanceof DOMException)) throw error
      console.error('Language preference could not be saved:', error)
      failed = true
    }
    setPreference({ language: next, storageError: failed })
  }

  function t(key: MessageKey, parameters?: Record<string, string | number>) {
    return translate(language, key, parameters)
  }

  function formatDate(value: string | null, seconds = false) {
    if (!value) return t('notProvided')
    return new Intl.DateTimeFormat(dateLocales[language], {
      timeZone: 'Asia/Hong_Kong', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', ...(seconds ? { second: '2-digit' as const } : {}), hour12: false,
    }).format(new Date(value))
  }

  function errorMessage(error: Error | MessageKey) {
    return t(typeof error === 'string' ? error : error instanceof ApiError ? error.code : 'requestError')
  }

  function checkExplanation(record: RecognitionRecord, check: RecognitionCheck) {
    if (['vision-llm-v1', 'vision-llm-burst-v1', 'vision-llm-v2', 'vision-llm-burst-v2', 'vision-llm-daily-v1'].includes(record.analyzer_version)) {
      if (check.category === 'signal' || check.category === 'scene') {
        if (check.explanation === 'Scene comparison requires an approved baseline. Camera movement was not assessed.') {
          return t('visionBaselineRequired')
        }
        if (check.explanation === 'Previous-day snapshot unavailable. Camera movement was not assessed.') {
          return t('visionPreviousDayUnavailable')
        }
        try {
          const parsed: unknown = JSON.parse(check.explanation)
          if (typeof parsed === 'object' && parsed !== null &&
              'en' in parsed && 'zh_hant' in parsed && 'zh_hans' in parsed &&
              typeof parsed.en === 'string' && typeof parsed.zh_hant === 'string' && typeof parsed.zh_hans === 'string') {
            const key = language === 'zh-Hant' ? 'zh_hant' : language === 'zh-Hans' ? 'zh_hans' : 'en'
            return `${parsed[key]}${check.category === 'signal' ? ` ${t('visionSignalLimitation')}` : ''}`
          }
        } catch (error) {
          console.error('Invalid vision explanation:', error)
        }
        return check.explanation
      }
      const translations: Record<string, MessageKey> = {
        'Timestamp comparison requires the actual capture time; upload time is not a substitute.': 'timestampCaptureRequired',
        'No unambiguous supported timestamp was read. Verify the camera timestamp manually.': 'visionTimestampUnreadable',
        'Timestamp drift is close to the threshold; verify the exact capture time manually.': 'timestampOcrBoundary',
      }
      const key = translations[check.explanation]
      if (key) return t(key)
      const drift = check.explanation.match(/^Camera timestamp differs from the supplied capture time by approximately (\d+) seconds\.$/)
        ?? check.explanation.match(/^Camera timestamp is within the configured drift threshold \(approximately (\d+) seconds\)\.$/)
      if (drift) return t(check.status === 'flagged' ? 'timestampOcrFlag' : 'timestampOcrPass', { seconds: drift[1] })
      return check.explanation
    }
    if (!['screenshot-statistics-v1', 'screenshot-ocr-v2', 'burst-statistics-v1'].includes(record.analyzer_version)) return check.explanation
    if (record.analyzer_version !== 'burst-statistics-v1' && check.category === 'signal' && check.status !== 'not_assessed') {
      return t(check.status === 'flagged' ? 'signalFlagExplanation' : 'signalPassExplanation')
    }
    if (record.analyzer_version === 'burst-statistics-v1' && check.category === 'signal') {
      const translations: Record<string, MessageKey> = {
        'Possible frozen or repeated frames. A stationary scene can look similar; verify the live feed.': 'burstRepeated',
        'Possible brightness flicker detected across the burst. Verify the live feed.': 'burstFlicker',
        'No black/blank, repeated-frame or flicker pattern was detected in this burst. No-signal overlays, freeze and flicker are not ruled out.': 'burstNoPattern',
        'Possible black or blank feed. Confirm against the original camera; an unlit scene can look similar. Confirm the burst and live feed manually.': 'burstBlank',
      }
      const key = translations[check.explanation]
      if (key) return t(key)
    }
    if (record.analyzer_version === 'screenshot-statistics-v1' && check.category === 'timestamp' && check.status === 'not_assessed') return t('timestampExplanation')
    if (record.analyzer_version !== 'screenshot-statistics-v1' && check.category === 'timestamp') {
      const translations: Record<string, MessageKey> = {
        'Timestamp OCR is not configured. Manual verification is required; upload time must not be used as the camera capture time.': 'timestampExplanation',
        'Timestamp comparison requires the actual capture time; upload time is not a substitute.': 'timestampCaptureRequired',
        'Timestamp OCR is unavailable or failed. Check the server logs and verify the camera timestamp manually.': 'timestampOcrUnavailable',
        'No unambiguous supported timestamp was read with sufficient confidence. Verify the camera timestamp manually.': 'timestampOcrUnreadable',
        'Timestamp drift is close to the threshold; verify the exact capture time manually.': 'timestampOcrBoundary',
      }
      const key = translations[check.explanation]
      if (key) return t(key)
      const drift = check.explanation.match(/^Camera timestamp differs from the supplied capture time by approximately (\d+) seconds\.$/)
        ?? check.explanation.match(/^Camera timestamp is within the configured drift threshold \(approximately (\d+) seconds\)\.$/)
      if (drift) return t(check.status === 'flagged' ? 'timestampOcrFlag' : 'timestampOcrPass', { seconds: drift[1] })
    }
    if (check.category === 'scene' && check.status === 'not_assessed') return t('sceneExplanation')
    return check.explanation
  }

  return { language, setLanguage, storageError, t, formatDate, errorMessage, checkExplanation }
}

export const LocaleContext = createContext<ReturnType<typeof useLanguageState> | null>(null)

export function useLocale() {
  const context = useContext(LocaleContext)
  if (!context) throw new Error('useLocale requires LanguageProvider.')
  return context
}
