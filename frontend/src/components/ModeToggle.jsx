export default function ModeToggle({ mode, onChange }) {
  return (
    <div className="mode-toggle">
      <button
        className={`mode-btn ${mode === 'autopilot' ? 'active' : ''}`}
        onClick={() => onChange('autopilot')}
      >
        Autopilot
      </button>
      <button
        className={`mode-btn ${mode === 'copilot' ? 'active' : ''}`}
        onClick={() => onChange('copilot')}
      >
        Co-pilot
      </button>
    </div>
  )
}
