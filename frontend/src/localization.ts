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
    document.title = `${translate(language, 'recognitionRecords')} | RetailVision`
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

  function formatDate(value: string | null) {
    if (!value) return t('notProvided')
    return new Intl.DateTimeFormat(dateLocales[language], {
      timeZone: 'Asia/Hong_Kong', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(new Date(value))
  }

  function errorMessage(error: Error | MessageKey) {
    return t(typeof error === 'string' ? error : error instanceof ApiError ? error.code : 'requestError')
  }

  function checkExplanation(record: RecognitionRecord, check: RecognitionCheck) {
    if (record.analyzer_version !== 'screenshot-statistics-v1') return check.explanation
    if (check.category === 'signal' && check.status !== 'not_assessed') {
      return t(check.status === 'flagged' ? 'signalFlagExplanation' : 'signalPassExplanation')
    }
    if (check.category === 'timestamp' && check.status === 'not_assessed') return t('timestampExplanation')
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
