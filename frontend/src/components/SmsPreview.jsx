import { useState } from 'react'

export default function SmsPreview({ state, data, mode, onApprove, onRetry }) {
  const [editedText, setEditedText] = useState('')

  const isPending = state === 'pending'
  const showEdit = isPending && mode === 'copilot'

  const handleSend = () => {
    onApprove(editedText || data?.sms_text || '')
  }

  return (
    <div className="card">
      <div className="card-header">
        SMS Notification
        {state === 'loading' && <span className="spinner" style={{ marginLeft: 'auto' }} />}
        {isPending && <span className="pending-badge">Draft - review before sending</span>}
      </div>
      <div className="card-body">
        {state === 'idle' && <div className="card-placeholder">Awaiting action selection...</div>}
        {state === 'loading' && <div className="text-muted">Drafting SMS...</div>}

        {state === 'error' && (
          <div className="card-error">
            <div>{data?.error || 'SMS generation failed'}</div>
            <button className="retry-btn" onClick={onRetry}>Retry</button>
          </div>
        )}

        {(state === 'success' || state === 'pending') && data && (
          <>
            <div className="phone-mockup">
              <div className="phone-notch" />
              {showEdit ? (
                <textarea
                  className="sms-edit-textarea"
                  value={editedText || data.sms_text}
                  onChange={e => setEditedText(e.target.value)}
                  rows={5}
                />
              ) : (
                <div className="sms-bubble">{data.sms_text}</div>
              )}
              <div className="sms-meta">
                {data.case_ref} &bull; {data.sent ? 'Sent' : 'Draft'}
              </div>
            </div>

            {showEdit ? (
              <div className="approve-bar" style={{ marginTop: 10 }}>
                <button className="approve-btn send-btn" onClick={handleSend}>Send SMS</button>
                <button className="retry-btn" onClick={onRetry}>Re-draft</button>
              </div>
            ) : state === 'success' ? (
              mode === 'autopilot' && (
                <div className="approve-bar auto" style={{ marginTop: 10 }}>
                  <span className="auto-label">Auto-sent</span>
                </div>
              )
            ) : null}
          </>
        )}
      </div>
    </div>
  )
}
