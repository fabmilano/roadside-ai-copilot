import { useState } from 'react'

export default function ClaimFields({ fields, customerRecord, mode, editable, onFieldEdit }) {
  const [editing, setEditing] = useState(false)
  const f = fields || {}
  const tier = customerRecord?.tier?.toUpperCase() || null
  const canEdit = editable && mode === 'copilot'

  const Field = ({ label, fieldKey, value, wide }) => {
    const isEditing = editing && canEdit && fieldKey
    return (
      <div className={`field-item${wide ? ' full-width' : ''}`}>
        <div className="field-label">{label}</div>
        {isEditing ? (
          <input
            className="field-edit-input"
            defaultValue={value != null ? String(value) : ''}
            onBlur={e => {
              const v = e.target.value.trim() || null
              if (v !== value) onFieldEdit(fieldKey, v)
            }}
          />
        ) : (
          <div className={`field-value${value == null ? ' empty' : ''}`}>
            {value != null ? String(value) : '-'}
          </div>
        )}
      </div>
    )
  }

  const policyDisplay = f.policy_number
    ? `${f.policy_number}${tier ? ` (${tier})` : ''}`
    : null

  return (
    <div className="card">
      <div className="card-header">
        Claim Details
        {canEdit && (
          <button
            className={`edit-toggle-btn ${editing ? 'active' : ''}`}
            onClick={() => setEditing(v => !v)}
            style={{ marginLeft: 'auto', fontSize: 11 }}
          >
            {editing ? 'Done' : 'Edit fields'}
          </button>
        )}
      </div>
      <div className="card-body">
        <div className="fields-grid">
          <Field label="Name" fieldKey="customer_name" value={f.customer_name} />
          <Field label="Policy" value={policyDisplay} />
          <Field label="Vehicle" fieldKey="vehicle_make" value={
            f.vehicle_make && f.vehicle_model
              ? `${f.vehicle_year || ''} ${f.vehicle_make} ${f.vehicle_model}`.trim()
              : null
          } />
          <Field label="Reg" fieldKey="vehicle_reg" value={f.vehicle_reg} />
          <Field label="Location" fieldKey="location_description" value={f.location_description} wide />
          <Field label="Incident" fieldKey="incident_type" value={f.incident_type} />
          <Field label="Drivable" fieldKey="vehicle_drivable" value={
            f.vehicle_drivable != null ? String(f.vehicle_drivable) : null
          } />
          <Field label="Passengers" fieldKey="passengers" value={f.passengers} />
          <Field label="Safe" value={f.is_safe != null ? String(f.is_safe) : null} />
          <Field label="Notes" fieldKey="notes" value={f.notes} />
        </div>
        {editing && canEdit && (
          <div className="field-edit-hint">
            Edit fields then retry coverage to re-run with updated details.
          </div>
        )}
      </div>
    </div>
  )
}
