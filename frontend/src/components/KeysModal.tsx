import { useState } from 'react'
import { saveUserKeys } from '../api'
import type { UserKeysOut } from '../types'

export function KeysModal({
  existing,
  onSave,
  onSkip,
}: {
  existing: UserKeysOut | null
  onSave: (keys: UserKeysOut) => void
  onSkip: () => void
}) {
  const [openaiKey, setOpenaiKey] = useState('')
  const [elKey, setElKey] = useState('')
  const [elVoice, setElVoice] = useState(existing?.elevenlabs_voice_id ?? '')
  const [elModel, setElModel] = useState(existing?.elevenlabs_model_id ?? 'eleven_multilingual_v2')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const saved = await saveUserKeys({
        openai_key: openaiKey.trim() || null,
        elevenlabs_key: elKey.trim() || null,
        elevenlabs_voice_id: elVoice.trim() || null,
        elevenlabs_model_id: elModel.trim() || null,
      })
      onSave(saved)
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="modal-overlay">
      <div className="modal-card modal-card-wide">
        <div className="modal-title">Configure API Keys</div>
        <div className="modal-subtitle">
          Your keys are encrypted before storage and used only for your generations.
        </div>

        <form onSubmit={save} className="auth-form">
          <div>
            <label>OpenAI API Key {existing?.has_openai_key && <span className="badge badge-audio" style={{ marginLeft: 6 }}>saved</span>}</label>
            <input
              type="password"
              placeholder={existing?.has_openai_key ? '••••••••••••••••••••••••••• (leave blank to keep)' : 'sk-…'}
              value={openaiKey}
              onChange={e => setOpenaiKey(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div>
            <label>ElevenLabs API Key {existing?.has_elevenlabs_key && <span className="badge badge-audio" style={{ marginLeft: 6 }}>saved</span>}</label>
            <input
              type="password"
              placeholder={existing?.has_elevenlabs_key ? '••••••••••••••••••••••••••• (leave blank to keep)' : 'your ElevenLabs key'}
              value={elKey}
              onChange={e => setElKey(e.target.value)}
              autoComplete="off"
            />
          </div>
          <div className="row">
            <div>
              <label>ElevenLabs Voice ID <span className="small">(optional)</span></label>
              <input
                placeholder="voice_id override"
                value={elVoice}
                onChange={e => setElVoice(e.target.value)}
              />
            </div>
            <div>
              <label>ElevenLabs Model ID <span className="small">(optional)</span></label>
              <input
                placeholder="eleven_multilingual_v2"
                value={elModel}
                onChange={e => setElModel(e.target.value)}
              />
            </div>
          </div>
          {error && <div className="error-text">{error}</div>}
          <div className="actions" style={{ marginTop: 8 }}>
            <button type="submit" disabled={loading}>
              {loading ? 'Saving…' : 'Save Keys'}
            </button>
            <button type="button" className="secondary" onClick={onSkip}>
              {existing?.has_openai_key ? 'Close' : 'Skip for now'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
