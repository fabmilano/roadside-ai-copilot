import { useState, useEffect, useRef, useCallback } from 'react'

const SPEECH_AVAILABLE = 'SpeechRecognition' in window || 'webkitSpeechRecognition' in window
const MAX_RECONNECT = 3
const RECONNECT_BASE_MS = 1000

export default function VoiceChat({ sessionId, onAgentResponse, onIntakeComplete, pipelineStage }) {
  const [messages, setMessages] = useState([])
  const [isListening, setIsListening] = useState(false)
  const [textInput, setTextInput] = useState('')
  const [ttsEnabled, setTtsEnabled] = useState(true)
  const [connected, setConnected] = useState(false)
  const [disconnected, setDisconnected] = useState(false)
  const [sending, setSending] = useState(false)

  const wsRef = useRef(null)
  const reconnectAttempts = useRef(0)
  const transcriptRef = useRef(null)
  const recognitionRef = useRef(null)
  const intakeCompleteRef = useRef(false)
  const introSpokenRef = useRef(false)
  const pendingIntroRef = useRef(null)

  const addMessage = useCallback((role, text) => {
    setMessages(prev => [...prev, { role, text, id: Date.now() + Math.random() }])
  }, [])

  const stopRecognition = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current._accumulated = ''
      try { recognitionRef.current.stop() } catch {}
      recognitionRef.current = null
    }
    setIsListening(false)
    setTextInput('')
  }, [])

  const speak = useCallback((text) => {
    if (!ttsEnabled || !window.speechSynthesis) return
    // Stop mic before TTS plays - prevents the mic capturing the agent's voice
    if (recognitionRef.current) {
      recognitionRef.current._accumulated = ''
      try { recognitionRef.current.stop() } catch {}
      recognitionRef.current = null
      setIsListening(false)
      setTextInput('')
    }
    window.speechSynthesis.cancel()
    const utter = new SpeechSynthesisUtterance(text)
    utter.lang = 'en-GB'
    utter.rate = 1.0
    window.speechSynthesis.speak(utter)
  }, [ttsEnabled])

  const sendMessage = useCallback((text) => {
    if (!text.trim() || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    setSending(true)
    addMessage('user', text)
    wsRef.current.send(JSON.stringify({ type: 'user_message', text }))
  }, [addMessage])

  const connect = useCallback(() => {
    if (!sessionId) return
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${window.location.host}/api/voice/${sessionId}`)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      setDisconnected(false)
      reconnectAttempts.current = 0
      const intro = "Hello, I'm your Allianz roadside assistance agent. I'm here to help you. Can you tell me your name and what's happened?"
      addMessage('agent', intro)
      if (!introSpokenRef.current) {
        // Queue it - browser blocks TTS until first user interaction (autoplay policy).
        // It will be spoken on the first click/keypress.
        pendingIntroRef.current = intro
      }
    }

    ws.onmessage = (event) => {
      setSending(false)
      // Stop mic the moment the agent responds - no risk of capturing TTS
      if (recognitionRef.current) {
        recognitionRef.current._accumulated = ''
        try { recognitionRef.current.stop() } catch {}
        recognitionRef.current = null
        setIsListening(false)
        setTextInput('')
      }
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'agent_response') {
          addMessage('agent', data.text)
          speak(data.text)
          if (onAgentResponse) onAgentResponse(data.extracted_fields, data.gates_fired)
          if (data.intake_complete && !intakeCompleteRef.current) {
            intakeCompleteRef.current = true
            if (onIntakeComplete) onIntakeComplete()
          }
        } else if (data.type === 'error') {
          addMessage('agent', `Error: ${data.text}`)
        }
      } catch (e) {
        console.error('WS parse error', e)
      }
    }

    ws.onerror = () => {
      setSending(false)
    }

    ws.onclose = () => {
      setConnected(false)
      setSending(false)
      if (intakeCompleteRef.current) return  // don't reconnect after intake is done
      const attempts = reconnectAttempts.current
      if (attempts < MAX_RECONNECT) {
        setDisconnected(true)
        reconnectAttempts.current = attempts + 1
        const delay = RECONNECT_BASE_MS * Math.pow(2, attempts)
        setTimeout(connect, delay)
      } else {
        setDisconnected(true)
      }
    }
  }, [sessionId, addMessage, speak, onAgentResponse, onIntakeComplete])

  useEffect(() => {
    if (sessionId) connect()
    return () => wsRef.current?.close()
  }, [sessionId])  // eslint-disable-line

  // Speak the queued intro on first user interaction (browsers block TTS before a gesture).
  useEffect(() => {
    const flush = () => {
      if (pendingIntroRef.current && !introSpokenRef.current) {
        introSpokenRef.current = true
        speak(pendingIntroRef.current)
        pendingIntroRef.current = null
      }
      document.removeEventListener('click', flush)
      document.removeEventListener('keydown', flush)
    }
    document.addEventListener('click', flush)
    document.addEventListener('keydown', flush)
    return () => {
      document.removeEventListener('click', flush)
      document.removeEventListener('keydown', flush)
    }
  }, [speak])

  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
    }
  }, [messages])

  const handleMic = () => {
    if (!SPEECH_AVAILABLE) return
    if (isListening) {
      const accumulated = recognitionRef.current?._accumulated || ''
      stopRecognition()
      if (accumulated.trim()) sendMessage(accumulated.trim())
      return
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    const rec = new SpeechRecognition()
    rec.lang = 'en-GB'
    rec.continuous = true      // keep listening until user stops manually
    rec.interimResults = true  // show live transcript in text input
    rec._accumulated = ''      // final-result accumulator

    rec.onresult = (e) => {
      let interim = ''
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const part = e.results[i][0].transcript
        if (e.results[i].isFinal) {
          rec._accumulated += (rec._accumulated ? ' ' : '') + part
        } else {
          interim += part
        }
      }
      // Show accumulated finals + current interim in the text box so user can see progress
      setTextInput((rec._accumulated + (interim ? ' ' + interim : '')).trim())
    }

    rec.onend = () => {
      setIsListening(false)
    }
    rec.onerror = () => {
      stopRecognition()
    }
    recognitionRef.current = rec
    rec.start()
    setIsListening(true)
    setTextInput('')
  }

  const handleTextSend = () => {
    if (!textInput.trim()) return
    sendMessage(textInput)
    setTextInput('')
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleTextSend()
    }
  }

  return (
    <div className="voice-chat">
      {disconnected && (
        <div className="disconnect-banner">
          Connection lost - attempting to reconnect...
        </div>
      )}

      <div className="transcript" ref={transcriptRef}>
        {messages.map(msg => (
          <div key={msg.id} className={`message ${msg.role}`}>
            <div className="message-label">{msg.role === 'agent' ? 'Agent' : 'You'}</div>
            {msg.text}
          </div>
        ))}
        {sending && (
          <div className="message agent">
            <div className="message-label">Agent</div>
            <span className="spinner" />
          </div>
        )}
      </div>

      {pipelineStage === 'complete' && (
        <div className="disconnect-banner" style={{ background: 'var(--green)', color: '#000' }}>
          Case complete - check your SMS for next steps.
        </div>
      )}

      <div className="voice-controls">
        {SPEECH_AVAILABLE && (
          <button
            className={`mic-btn ${isListening ? 'listening' : ''}`}
            onClick={handleMic}
            disabled={!connected || sending || pipelineStage === 'complete'}
            title={isListening ? 'Stop listening' : 'Start voice input'}
          >
            {isListening ? '⬛' : '🎤'}
          </button>
        )}
        <div className="text-input-row">
          <input
            value={textInput}
            onChange={e => setTextInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={pipelineStage === 'complete' ? 'Chat closed.' : 'Type your message...'}
            disabled={!connected || sending || pipelineStage === 'complete'}
          />
          <button
            onClick={handleTextSend}
            disabled={!connected || sending || !textInput.trim() || pipelineStage === 'complete'}
          >
            Send
          </button>
        </div>
        <button
          className={`tts-toggle ${ttsEnabled ? 'on' : ''}`}
          onClick={() => setTtsEnabled(v => !v)}
          title="Toggle text-to-speech"
        >
          {ttsEnabled ? '🔊' : '🔇'}
        </button>
      </div>
    </div>
  )
}
