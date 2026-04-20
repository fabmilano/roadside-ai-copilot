import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet'
import L from 'leaflet'
import { useEffect, useState } from 'react'

delete L.Icon.Default.prototype._getIconUrl
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
})

const redIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-red.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41],
})

const greenIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-green.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34], shadowSize: [41, 41],
})

function FitBounds({ positions }) {
  const map = useMap()
  useEffect(() => {
    if (positions.length >= 2) map.fitBounds(positions, { padding: [30, 30] })
  }, [positions, map])
  return null
}

function ApproveBar({ mode, onApprove, onRetry }) {
  if (mode !== 'copilot') return (
    <div className="approve-bar auto">
      <span className="auto-label">Auto-approved</span>
      <button className="retry-btn" onClick={onRetry}>Retry</button>
    </div>
  )
  return (
    <div className="approve-bar">
      <button className="approve-btn" onClick={onApprove}>Approve</button>
      <button className="retry-btn" onClick={onRetry}>Retry</button>
    </div>
  )
}

const ONWARD_LABELS = {
  hire_car: 'Hire Car',
  rail: 'Rail Fare',
  hotel: 'Hotel',
  none: 'None',
}

const ONWARD_ICONS = {
  hire_car: '🚗',
  rail: '🚆',
  hotel: '🏨',
  none: '—',
}

export default function ActionResult({ state, data, extractedFields, mode, onApprove, onRetry }) {
  const [isEditing, setIsEditing] = useState(false)
  const [editRecovery, setEditRecovery] = useState('tow')
  const [editOnward, setEditOnward] = useState('none')
  const [editGarageIdx, setEditGarageIdx] = useState(0)
  const customerLat = extractedFields?.location_lat
  const customerLng = extractedFields?.location_lng

  const startEdit = () => {
    setEditRecovery(data?.recovery_action || 'tow')
    setEditOnward(data?.onward_travel || 'none')
    setEditGarageIdx(0)
    setIsEditing(true)
  }
  const cancelEdit = () => setIsEditing(false)

  const buildApproved = () => {
    const selectedGarage = data?.top_garages?.[editGarageIdx] || data?.garage
    return { ...data, recovery_action: editRecovery, onward_travel: editOnward, garage: selectedGarage }
  }

  const displayGarage = (isEditing && state === 'pending' && mode === 'copilot')
    ? (data?.top_garages?.[editGarageIdx] || data?.garage)
    : data?.garage

  const showEdit = state === 'pending' && mode === 'copilot'

  const recoveryAction = data?.recovery_action || 'none'
  const onwardTravel = data?.onward_travel || 'none'
  const onwardOptions = data?.onward_travel_options || []
  const onwardSelectOptions = [...new Set([...onwardOptions, 'none'])]

  const bothNone = recoveryAction === 'none' && onwardTravel === 'none'

  return (
    <div className="card">
      <div className="card-header">
        Recommended Action
        {state === 'loading' && <span className="spinner" style={{ marginLeft: 'auto' }} />}
        {state === 'pending' && <span className="pending-badge">Awaiting approval</span>}
      </div>
      <div className="card-body">
        {state === 'idle' && <div className="card-placeholder">Awaiting coverage check...</div>}
        {state === 'loading' && <div className="text-muted">Selecting garage...</div>}

        {state === 'error' && (
          <div className="card-error">
            <div>{data?.error || 'Action selection failed'}</div>
            <button className="retry-btn" onClick={onRetry}>Retry</button>
          </div>
        )}

        {(state === 'success' || state === 'pending') && data && bothNone && (
          <div className="text-muted" style={{ padding: '8px 0' }}>
            No dispatch required - see SMS for next steps.
          </div>
        )}

        {(state === 'success' || state === 'pending') && data && !bothNone && (
          <>
            {/* Row 1: Recovery dispatch */}
            <div className="field-label" style={{ marginBottom: 4 }}>Recovery dispatch</div>
            <div className="action-header-row" style={{ marginBottom: 10 }}>
              {showEdit && isEditing ? (
                <select
                  className="edit-select"
                  value={editRecovery}
                  onChange={e => setEditRecovery(e.target.value)}
                >
                  <option value="tow">Tow Required</option>
                  <option value="mobile_repair">Mobile Repair</option>
                  <option value="none">No recovery</option>
                </select>
              ) : (
                <>
                  <span style={{ fontSize: 20 }}>
                    {recoveryAction === 'tow' ? '🚛' : recoveryAction === 'mobile_repair' ? '🔧' : '—'}
                  </span>
                  <div className="action-type">
                    {recoveryAction === 'tow' ? 'Tow Required' : recoveryAction === 'mobile_repair' ? 'Mobile Repair' : 'No recovery'}
                  </div>
                </>
              )}
              {recoveryAction !== 'none' && (
                <span style={{ fontSize: 13, color: 'var(--yellow)', marginLeft: 'auto' }}>
                  ETA ~{data.estimated_response_minutes} min
                </span>
              )}
            </div>

            {showEdit && isEditing && data.top_garages?.length > 1 && (editRecovery === 'tow' || editRecovery === 'mobile_repair') && (
              <div style={{ marginBottom: 10 }}>
                <div className="field-label" style={{ marginBottom: 4 }}>Select garage</div>
                <select
                  className="edit-select"
                  value={editGarageIdx}
                  onChange={e => setEditGarageIdx(Number(e.target.value))}
                >
                  {data.top_garages.map((g, i) => (
                    <option key={i} value={i}>
                      {g.name} - {g.distance_miles} mi
                    </option>
                  ))}
                </select>
              </div>
            )}

            {displayGarage && ((!isEditing && recoveryAction !== 'none') || (isEditing && (editRecovery === 'tow' || editRecovery === 'mobile_repair'))) && (
              <div className="garage-info" style={{ marginBottom: 10 }}>
                <div className="garage-name">{displayGarage.name}</div>
                <div className="garage-meta">
                  <span>{displayGarage.distance_miles} miles</span>
                  <span>{displayGarage.hours}</span>
                </div>
              </div>
            )}

            {/* Row 2: Onward travel */}
            <div className="field-label" style={{ marginBottom: 4, marginTop: 4 }}>Onward travel</div>
            <div className="action-header-row" style={{ marginBottom: 10 }}>
              {showEdit && isEditing ? (
                <select
                  className="edit-select"
                  value={editOnward}
                  onChange={e => setEditOnward(e.target.value)}
                >
                  {onwardSelectOptions.map(opt => (
                    <option key={opt} value={opt}>{ONWARD_LABELS[opt] || opt}</option>
                  ))}
                  {!onwardSelectOptions.includes('none') && <option value="none">None</option>}
                </select>
              ) : (
                <>
                  <span style={{ fontSize: 20 }}>{ONWARD_ICONS[onwardTravel] || '—'}</span>
                  <div className="action-type">{ONWARD_LABELS[onwardTravel] || onwardTravel}</div>
                </>
              )}
            </div>

            {data.reasoning && (
              <div className="reasoning-text" style={{ marginBottom: 10 }}>{data.reasoning}</div>
            )}

            {displayGarage?.lat && customerLat && recoveryAction !== 'none' && (
              <div className="map-container">
                <MapContainer
                  center={[displayGarage.lat, displayGarage.lng]}
                  zoom={13}
                  style={{ height: '100%', width: '100%' }}
                  zoomControl
                >
                  <TileLayer
                    url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    attribution='&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>'
                  />
                  <Marker position={[customerLat, customerLng]} icon={redIcon}>
                    <Popup>Customer location</Popup>
                  </Marker>
                  <Marker position={[displayGarage.lat, displayGarage.lng]} icon={greenIcon}>
                    <Popup>{displayGarage.name}</Popup>
                  </Marker>
                  <FitBounds positions={[[customerLat, customerLng], [displayGarage.lat, displayGarage.lng]]} />
                </MapContainer>
              </div>
            )}

            {(state === 'success' || state === 'pending') && (
              <div style={{ marginTop: 8 }}>
                {showEdit && isEditing ? (
                  <div className="approve-bar">
                    <button className="approve-btn" onClick={() => { onApprove(buildApproved()); cancelEdit() }}>
                      Approve
                    </button>
                    <button className="edit-toggle-btn active" onClick={cancelEdit}>Cancel edit</button>
                    <button className="retry-btn" onClick={onRetry}>Retry</button>
                  </div>
                ) : (
                  <ApproveBar
                    mode={state === 'pending' ? mode : 'autopilot'}
                    onApprove={() => {
                      if (mode === 'copilot' && state === 'pending') {
                        onApprove(data)
                      }
                    }}
                    onRetry={onRetry}
                  />
                )}
                {showEdit && !isEditing && (
                  <button className="edit-toggle-btn" onClick={startEdit} style={{ marginTop: 6 }}>
                    Edit dispatch
                  </button>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
