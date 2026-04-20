import { useState, useEffect, useCallback, useRef } from 'react'
import VoiceChat from './components/VoiceChat.jsx'
import Dashboard from './components/Dashboard.jsx'
import PipelineStatus from './components/PipelineStatus.jsx'
import ModeToggle from './components/ModeToggle.jsx'

const IDLE = 'idle'
const LOADING = 'loading'
const SUCCESS = 'success'
const ERROR = 'error'
const PENDING = 'pending'

function cardState(state, data) { return { state, data } }

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [pipelineStage, setPipelineStage] = useState('intake')
  const [extractedFields, setExtractedFields] = useState({})
  const [customerRecord, setCustomerRecord] = useState(null)
  const [coverage, setCoverage] = useState(cardState(IDLE, null))
  const [action, setAction] = useState(cardState(IDLE, null))
  const [sms, setSms] = useState(cardState(IDLE, null))
  const [mode, setModeState] = useState('autopilot')
  const [gates, setGates] = useState([])
  const [intakeComplete, setIntakeComplete] = useState(false)
  const pipelineRunning = useRef(false)
  const modeRef = useRef('autopilot')

  const setMode = (m) => { modeRef.current = m; setModeState(m) }

  useEffect(() => {
    fetch('/api/session/start', { method: 'POST' })
      .then(r => r.json())
      .then(d => setSessionId(d.session_id))
      .catch(console.error)
  }, [])

  const handleAgentResponse = useCallback((fields, gatesFired) => {
    if (fields) setExtractedFields(fields)
    if (gatesFired) setGates(gatesFired)
  }, [])

  const delay = (ms) => new Promise(r => setTimeout(r, ms))

  const runCoverage = useCallback(async (sid) => {
    setCoverage(cardState(LOADING, null))
    setPipelineStage('coverage')
    await delay(800)
    try {
      const res = await fetch(`/api/check-coverage/${sid}`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) { setCoverage(cardState(ERROR, data)); return null }
      const sess = await fetch(`/api/session/${sid}`).then(r => r.json())
      if (sess.customer_record) setCustomerRecord(sess.customer_record)
      if (data.auto_approved) {
        setCoverage(cardState(SUCCESS, data))
        return data
      }
      setCoverage(cardState(PENDING, data))
      return null
    } catch (e) {
      setCoverage(cardState(ERROR, { error: e.message }))
      return null
    }
  }, [])

  const runAction = useCallback(async (sid) => {
    setAction(cardState(LOADING, null))
    setPipelineStage('action')
    await delay(1000)
    try {
      const res = await fetch(`/api/next-action/${sid}`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) { setAction(cardState(ERROR, data)); return null }
      const sess = await fetch(`/api/session/${sid}`).then(r => r.json())
      if (sess.extracted_fields) setExtractedFields(sess.extracted_fields)
      if (data.auto_approved) {
        setAction(cardState(SUCCESS, data))
        return data
      }
      setAction(cardState(PENDING, data))
      return null
    } catch (e) {
      setAction(cardState(ERROR, { error: e.message }))
      return null
    }
  }, [])

  const runNotify = useCallback(async (sid) => {
    setSms(cardState(LOADING, null))
    setPipelineStage('notify')
    await delay(800)
    try {
      const res = await fetch(`/api/notify/${sid}`, { method: 'POST' })
      const data = await res.json()
      if (!res.ok) { setSms(cardState(ERROR, data)); return }
      if (data.auto_approved) {
        setSms(cardState(SUCCESS, data))
        setPipelineStage('complete')
      } else {
        setSms(cardState(PENDING, data))
      }
    } catch (e) {
      setSms(cardState(ERROR, { error: e.message }))
    }
  }, [])

  const handleIntakeComplete = useCallback(async () => {
    if (pipelineRunning.current || !sessionId) return
    pipelineRunning.current = true
    setIntakeComplete(true)
    const coverageData = await runCoverage(sessionId)
    if (!coverageData) { pipelineRunning.current = false; return }
    const actionData = await runAction(sessionId)
    if (!actionData) { pipelineRunning.current = false; return }
    await runNotify(sessionId)
    pipelineRunning.current = false
  }, [sessionId, runCoverage, runAction, runNotify])

  const handleApproveCoverage = useCallback(async (approvedData) => {
    if (!sessionId) return
    await fetch(`/api/session/${sessionId}/approve/coverage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edited: approvedData }),
    })
    setCoverage(cardState(SUCCESS, approvedData))
    const actionData = await runAction(sessionId)
    if (!actionData) return
    await runNotify(sessionId)
  }, [sessionId, runAction, runNotify])

  const handleApproveAction = useCallback(async (approvedData) => {
    if (!sessionId) return
    await fetch(`/api/session/${sessionId}/approve/action`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edited: approvedData }),
    })
    setAction(cardState(SUCCESS, approvedData))
    await runNotify(sessionId)
  }, [sessionId, runNotify])

  const handleApproveSms = useCallback(async (smsText) => {
    if (!sessionId) return
    await fetch(`/api/session/${sessionId}/approve/notify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ edited: { sms_text: smsText } }),
    })
    setSms(prev => cardState(SUCCESS, { ...prev.data, sms_text: smsText, sent: true }))
    setPipelineStage('complete')
  }, [sessionId])

  const handleFieldEdit = useCallback(async (key, value) => {
    setExtractedFields(prev => ({ ...prev, [key]: value }))
    if (!sessionId) return
    await fetch(`/api/session/${sessionId}/fields`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [key]: value }),
    })
  }, [sessionId])

  const handleModeChange = useCallback(async (newMode) => {
    setMode(newMode)
    if (!sessionId) return
    const res = await fetch(`/api/session/${sessionId}/mode`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: newMode }),
    })
    const { auto_approved_stages } = await res.json()
    if (newMode === 'autopilot' && auto_approved_stages.length > 0) {
      if (auto_approved_stages.includes('coverage')) {
        setCoverage(prev => ({ ...prev, state: SUCCESS }))
        const actionData = await runAction(sessionId)
        if (actionData) await runNotify(sessionId)
      } else if (auto_approved_stages.includes('action')) {
        setAction(prev => ({ ...prev, state: SUCCESS }))
        await runNotify(sessionId)
      } else if (auto_approved_stages.includes('notify')) {
        await fetch(`/api/session/${sessionId}/approve/notify`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
        })
        setSms(prev => cardState(SUCCESS, { ...prev.data, sent: true }))
        setPipelineStage('complete')
      }
    }
  }, [sessionId, runAction, runNotify])

  const handleRetryCoverage = useCallback(async () => {
    if (!sessionId) return
    pipelineRunning.current = true
    const coverageData = await runCoverage(sessionId)
    if (!coverageData) { pipelineRunning.current = false; return }
    const actionData = await runAction(sessionId)
    if (!actionData) { pipelineRunning.current = false; return }
    await runNotify(sessionId)
    pipelineRunning.current = false
  }, [sessionId, runCoverage, runAction, runNotify])

  const handleRetryAction = useCallback(async () => {
    if (!sessionId) return
    const actionData = await runAction(sessionId)
    if (!actionData) return
    await runNotify(sessionId)
  }, [sessionId, runAction, runNotify])

  const handleRetrySms = useCallback(async () => {
    if (!sessionId) return
    await runNotify(sessionId)
  }, [sessionId, runNotify])

  if (!sessionId) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh' }}>
        <span className="spinner" /> &nbsp; Connecting...
      </div>
    )
  }

  return (
    <>
      <div className="header">
        <h1>ALLIANCE ROADSIDE CO-PILOT</h1>
        <ModeToggle mode={mode} onChange={handleModeChange} />
        <div className="header-right">
          <PipelineStatus stage={pipelineStage} />
        </div>
      </div>
      <div className="main-layout">
        <div className="left-panel">
          <div className="panel-label">Customer simulator &mdash; in production this is a phone call</div>
          <VoiceChat
            sessionId={sessionId}
            onAgentResponse={handleAgentResponse}
            onIntakeComplete={handleIntakeComplete}
            pipelineStage={pipelineStage}
          />
        </div>
        <div className="right-panel">
          <div className="panel-label">Operator console</div>
          <Dashboard
            sessionId={sessionId}
            mode={mode}
            gates={gates}
            extractedFields={extractedFields}
            customerRecord={customerRecord}
            intakeComplete={intakeComplete}
            coverage={coverage}
            action={action}
            sms={sms}
            onFieldEdit={handleFieldEdit}
            onApproveCoverage={handleApproveCoverage}
            onApproveAction={handleApproveAction}
            onApproveSms={handleApproveSms}
            onRetryCoverage={handleRetryCoverage}
            onRetryAction={handleRetryAction}
            onRetrySms={handleRetrySms}
          />
        </div>
      </div>
    </>
  )
}
