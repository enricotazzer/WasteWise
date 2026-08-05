import { useEffect, useRef, useState } from 'react'

import { type Decoder, cameraAvailable, createDecoder, hasNativeDetector } from '../lib/barcode'

interface Props {
  onDetected: (barcode: string) => void
  /** Pauses the decode loop (not the camera) while a confirm sheet is open. */
  paused?: boolean
  onClose: () => void
}

// Same code seen again within this window is the same physical item still in frame, not a
// second scan. Long enough to swap products, short enough to rescan deliberately.
const DEDUPE_MS = 2500

/**
 * Continuous camera scanning.
 *
 * The camera stays open and codes accumulate as you unpack the shopping — twelve items
 * should be twelve scans, not twelve navigations. That's the whole reason this is worth
 * building over just typing.
 */
export function Scanner({ onDetected, paused = false, onClose }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const decoderRef = useRef<Decoder | null>(null)
  const lastHit = useRef<{ code: string; at: number }>({ code: '', at: 0 })
  const rafRef = useRef<number | null>(null)
  const pausedRef = useRef(paused)
  const [error, setError] = useState<string | null>(null)
  const [ready, setReady] = useState(false)

  // Read through a ref inside the loop: re-subscribing on every pause toggle would tear
  // down and reopen the camera, which takes a visible second on a phone.
  pausedRef.current = paused

  useEffect(() => {
    if (!cameraAvailable()) {
      setError(
        window.isSecureContext
          ? 'Questo browser non espone la fotocamera.'
          : 'La fotocamera richiede HTTPS. Da telefono usa `make dev-https` e apri https://<ip>:5173.',
      )
      return
    }

    let stream: MediaStream | null = null
    let cancelled = false
    const decoder = createDecoder()
    decoderRef.current = decoder

    async function start() {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: {
            facingMode: { ideal: 'environment' },
            width: { ideal: 1280 },
            height: { ideal: 720 },
          },
          audio: false,
        })
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop())
          return
        }
        const video = videoRef.current
        if (!video) return
        video.srcObject = stream
        await video.play()
        setReady(true)
        loop()
      } catch (err) {
        setError(
          err instanceof DOMException && err.name === 'NotAllowedError'
            ? 'Permesso fotocamera negato. Consentilo nelle impostazioni del browser.'
            : 'Impossibile aprire la fotocamera.',
        )
      }
    }

    let busy = false
    async function loop() {
      if (cancelled) return
      const video = videoRef.current
      // Decoding is async and can outlast a frame; `busy` stops the callbacks piling up.
      if (video && !busy && !pausedRef.current && video.readyState >= 2) {
        busy = true
        try {
          const codes = await decoder.detect(video)
          const code = codes[0]
          const now = Date.now()
          if (code && !(code === lastHit.current.code && now - lastHit.current.at < DEDUPE_MS)) {
            lastHit.current = { code, at: now }
            onDetected(code)
          }
        } catch {
          /* a frame that fails to decode is the normal case, not an error */
        } finally {
          busy = false
        }
      }
      rafRef.current = requestAnimationFrame(loop)
    }

    void start()

    return () => {
      cancelled = true
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
      stream?.getTracks().forEach((t) => t.stop())
      decoder.close()
    }
  }, [onDetected])

  if (error) {
    return (
      <div className="card scanner-error">
        <p>{error}</p>
        <button onClick={onClose}>Chiudi</button>
      </div>
    )
  }

  return (
    <div className="scanner">
      <video ref={videoRef} playsInline muted autoPlay />
      <div className="scanner-frame" aria-hidden="true" />
      <div className="scanner-bar">
        <span className="scanner-hint">
          {ready ? 'Inquadra il codice a barre' : 'Apro la fotocamera…'}
          {ready && (
            <em className="scanner-engine">
              {hasNativeDetector() ? 'nativo' : 'wasm'}
            </em>
          )}
        </span>
        <button onClick={onClose}>Chiudi</button>
      </div>
    </div>
  )
}
