import { useState } from 'react'

function CitationChip({ section, snippet }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="citation-chip" onClick={() => setOpen(v => !v)} title={open ? 'Click to collapse' : 'Click to expand'}>
      §{section}
      {open && <span className="citation-snippet">{snippet}</span>}
    </span>
  )
}

function ApproveBar({ data, mode, onApprove, onRetry, editing, onEditToggle }) {
  if (mode !== 'copilot') return (
    <div className="approve-bar auto">
      <span className="auto-label">Auto-approved</span>
      <button className="retry-btn" onClick={onRetry}>Retry</button>
    </div>
  )
  return (
    <div className="approve-bar">
      <button className="approve-btn" onClick={() => onApprove(data)}>Approve</button>
      <button className={`edit-toggle-btn ${editing ? 'active' : ''}`} onClick={onEditToggle}>
        {editing ? 'Cancel edit' : 'Edit'}
      </button>
      <button className="retry-btn" onClick={onRetry}>Retry</button>
    </div>
  )
}

export default function CoverageResult({ state, data, mode, onApprove, onRetry }) {
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState(null)

  const startEdit = () => {
    setEditData({
      ...data,
      services_entitled: [...(data?.services_entitled || [])],
    })
    setEditing(true)
  }
  const cancelEdit = () => { setEditing(false); setEditData(null) }

  const handleApprove = () => {
    onApprove(editing && editData ? editData : data)
    setEditing(false)
    setEditData(null)
  }

  const displayData = editing && editData ? editData : data

  return (
    <div className="card">
      <div className="card-header">
        Coverage Check
        {state === 'loading' && <span className="spinner" style={{ marginLeft: 'auto' }} />}
        {state === 'pending' && <span className="pending-badge">Awaiting approval</span>}
      </div>
      <div className="card-body">
        {state === 'idle' && <div className="card-placeholder">Awaiting intake completion...</div>}
        {state === 'loading' && <div className="text-muted">Checking policy...</div>}

        {state === 'error' && (
          <div className="card-error">
            <div>{data?.error || 'Coverage check failed'}</div>
            <button className="retry-btn" onClick={onRetry}>Retry</button>
          </div>
        )}

        {(state === 'success' || state === 'pending') && displayData && (
          <>
            <div className="coverage-verdict">
              {editing ? (
                <label className="edit-covered">
                  <input
                    type="checkbox"
                    checked={editData?.covered ?? false}
                    onChange={e => setEditData(d => ({ ...d, covered: e.target.checked }))}
                  />
                  <span className={`badge ${editData?.covered ? 'covered' : 'not-covered'}`}>
                    {editData?.covered ? '✓ Covered' : '✗ Not Covered'}
                  </span>
                </label>
              ) : (
                <span className={`badge ${displayData.covered ? 'covered' : 'not-covered'}`}>
                  {displayData.covered ? '✓ Covered' : '✗ Not Covered'}
                </span>
              )}
              <span style={{ fontSize: 13 }}>{displayData.event_type}</span>
              {displayData.citations?.map((c, i) => (
                <CitationChip key={i} section={c.section} snippet={c.snippet} />
              ))}
            </div>

            {displayData.applicable_section && (
              <div className="coverage-section">{displayData.applicable_section}</div>
            )}

            {editing ? (
              <div className="edit-services">
                <div className="field-label" style={{ marginBottom: 4 }}>Services entitled (one per line)</div>
                <textarea
                  className="edit-textarea"
                  value={(editData?.services_entitled || []).join('\n')}
                  onChange={e => setEditData(d => ({
                    ...d,
                    services_entitled: e.target.value.split('\n').filter(Boolean)
                  }))}
                  rows={4}
                />
              </div>
            ) : (
              displayData.services_entitled?.length > 0 && (
                <ul className="services-list">
                  {displayData.services_entitled.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              )
            )}

            {displayData.exclusions_flagged?.length > 0 && (
              <ul className="services-list exclusions-list">
                {displayData.exclusions_flagged.map((e, i) => <li key={i}>{e}</li>)}
              </ul>
            )}

            {editing ? (
              <textarea
                className="edit-textarea"
                value={editData?.reasoning || ''}
                onChange={e => setEditData(d => ({ ...d, reasoning: e.target.value }))}
                rows={3}
                placeholder="Reasoning..."
                style={{ marginTop: 8 }}
              />
            ) : (
              displayData.reasoning && (
                <div className="reasoning-text">{displayData.reasoning}</div>
              )
            )}

            <ApproveBar
              data={editing ? editData : data}
              mode={mode}
              onApprove={handleApprove}
              onRetry={onRetry}
              editing={editing}
              onEditToggle={editing ? cancelEdit : startEdit}
            />
          </>
        )}
      </div>
    </div>
  )
}
