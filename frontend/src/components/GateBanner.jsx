const TYPE_LABELS = {
  emergency: { label: 'Emergency', cls: 'gate-emergency' },
  policy_validation: { label: 'Policy gate', cls: 'gate-policy' },
  vehicle_mismatch: { label: 'Vehicle mismatch', cls: 'gate-mismatch' },
}

export default function GateBanner({ gates }) {
  if (!gates || gates.length === 0) return null
  return (
    <div className="gate-banner">
      <div className="gate-banner-title">Gate events</div>
      {gates.map((g, i) => {
        const meta = TYPE_LABELS[g.type] || { label: g.type, cls: 'gate-policy' }
        const time = new Date(g.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
        return (
          <div key={i} className={`gate-entry ${meta.cls}`}>
            <span className="gate-label">{meta.label}</span>
            <span className="gate-summary">{g.summary}</span>
            <span className="gate-time">{time}</span>
          </div>
        )
      })}
    </div>
  )
}
