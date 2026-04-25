import { useRef, useState } from 'react'
import { resolveImageUrl, uploadSceneImage } from '../api'
import type { ImageAssetRef, StoryboardScene } from '../types'
import { assetBadgeClass, fmtSeconds } from '../utils'
import type { ToastKind } from './Toast'

export function SceneCard({
  scene,
  imageRef,
  articleId,
  onImageUploaded,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
  onToast,
}: {
  scene: StoryboardScene
  imageRef?: ImageAssetRef
  articleId?: string
  onImageUploaded?: () => void
  onMoveUp?: () => void
  onMoveDown?: () => void
  isFirst?: boolean
  isLast?: boolean
  onToast?: (kind: ToastKind, msg: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadErr, setUploadErr] = useState('')

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !articleId) return
    setUploading(true)
    setUploadErr('')
    try {
      await uploadSceneImage(articleId, scene.scene_number, file)
      onToast?.('success', `Image uploaded for scene ${scene.scene_number}`)
      onImageUploaded?.()
    } catch (err: unknown) {
      const msg = String((err as Error)?.message ?? err)
      setUploadErr(msg)
      onToast?.('error', `Scene ${scene.scene_number}: ${msg}`)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="scene-card">
      <div className="scene-card-header">
        <span className="scene-num">Scene {scene.scene_number}</span>
        <span className={assetBadgeClass(scene.asset_type)}>{scene.asset_type}</span>
        <span className="scene-time">{fmtSeconds(scene.start_time_estimate)}</span>
        <span className="scene-dur">{fmtSeconds(scene.duration_estimate)}</span>
        {(onMoveUp || onMoveDown) && (
          <div className="scene-reorder">
            <button
              className="secondary settings-btn"
              style={{ padding: '2px 7px', fontSize: 11, opacity: isFirst ? 0.3 : 1 }}
              onClick={onMoveUp}
              disabled={isFirst}
              title="Move up"
            >↑</button>
            <button
              className="secondary settings-btn"
              style={{ padding: '2px 7px', fontSize: 11, opacity: isLast ? 0.3 : 1 }}
              onClick={onMoveDown}
              disabled={isLast}
              title="Move down"
            >↓</button>
          </div>
        )}
      </div>
      <div className="scene-card-content">
        <div className="scene-card-text">
          {scene.on_screen_text && (
            <div className="scene-overlay">"{scene.on_screen_text}"</div>
          )}
          <div className="scene-narration">{scene.narration}</div>
          <div className="scene-visual small">{scene.visual_prompt}</div>
          {articleId && (
            <div style={{ marginTop: 6 }}>
              <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} onChange={handleFile} />
              <button
                className="secondary settings-btn"
                style={{ fontSize: 11, padding: '3px 8px' }}
                onClick={() => fileRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? 'Uploading…' : imageRef?.status === 'ready' ? 'Replace Image' : 'Upload Image'}
              </button>
              {uploadErr && <div className="error-text" style={{ fontSize: 11, marginTop: 2 }}>{uploadErr}</div>}
            </div>
          )}
        </div>
        {imageRef?.status === 'ready' && (
          <a href={resolveImageUrl(imageRef.id)} target="_blank" rel="noreferrer" className="scene-thumb-link">
            <img
              className="scene-thumb"
              src={resolveImageUrl(imageRef.id)}
              alt={`Scene ${scene.scene_number}`}
              loading="lazy"
            />
          </a>
        )}
      </div>
    </div>
  )
}
