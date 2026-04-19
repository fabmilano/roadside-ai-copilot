export default function PipelineStatus({ stage }) {
  const steps = ['intake', 'coverage', 'action', 'notify']
  const labels = { intake: 'Intake', coverage: 'Coverage', action: 'Action', notify: 'Notify' }
  const currentIdx = steps.indexOf(stage)

  return (
    <div className="pipeline-bar">
      {steps.map((s, i) => {
        const isDone = i < currentIdx || stage === 'complete'
        const isActive = s === stage && stage !== 'complete'
        return (
          <div key={s} style={{ display: 'flex', alignItems: 'center' }}>
            <div className={`pipeline-step ${isDone ? 'done' : ''} ${isActive ? 'active' : ''}`}>
              <div className="pipeline-dot" />
              <span>{labels[s]}</span>
            </div>
            {i < steps.length - 1 && (
              <div className={`pipeline-line ${isDone ? 'done' : ''}`} />
            )}
          </div>
        )
      })}
    </div>
  )
}
