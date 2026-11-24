import type { ReactNode } from 'react'
import { LocaleContext, useLanguageState } from './localization'

export function LanguageProvider({ children }: { children: ReactNode }) {
  const value = useLanguageState()
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

