import { useState } from 'react'
import { evidenceUrl } from '../api'
import { Icon } from './Icon'
import { useLocale } from '../localization'

export function EvidenceImage({ id, alt, thumbnail = false }: {
  id: string; alt: string; thumbnail?: boolean
}) {
  const { t } = useLocale()
  const [failed, setFailed] = useState(false)
  return <div className={thumbnail ? 'evidence-thumbnail' : 'evidence-image'}>
    {failed ? <span className="image-failure"><Icon name="image" />
      {!thumbnail && t('evidenceError')}</span> :
      <img src={evidenceUrl(id)} alt={alt} loading={thumbnail ? 'lazy' : 'eager'}
        onError={() => setFailed(true)} />}
  </div>
}
