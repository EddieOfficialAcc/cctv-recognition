import { useLocale } from '../localization'

export function Brand() {
  const { t } = useLocale()
  return <>
    <span className="brand-mark" aria-hidden="true">360°</span>
    <span className="brand-copy">BEST MART <span className="brand-accent">360°</span>
      <small>RetailVision · {t('platform')}</small>
    </span>
  </>
}
