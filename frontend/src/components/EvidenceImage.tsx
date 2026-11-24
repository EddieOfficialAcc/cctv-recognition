import { useState } from 'react'
import { burstFrameUrl, evidenceUrl } from '../api'
import { Icon } from './Icon'
import { useLocale } from '../localization'

export function EvidenceImage({ id, alt, thumbnail = false, frameIndex = 0 }: {
  id: string; alt: string; thumbnail?: boolean; frameIndex?: number
}) {
  const { t } = useLocale()
  const [failed, setFailed] = useState(false)
  return <div className={thumbnail ? 'evidence-thumbnail' : 'evidence-image'}>
    {failed ? <span className="image-failure"><Icon name="image" />
      {!thumbnail && t('evidenceError')}</span> :
      <img src={frameIndex ? burstFrameUrl(id, frameIndex) : evidenceUrl(id)} alt={alt} loading={thumbnail ? 'lazy' : 'eager'}
        onError={() => setFailed(true)} />}
  </div>
}
