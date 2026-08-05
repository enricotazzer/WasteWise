/**
 * Barcode decoding, with the iOS problem solved.
 *
 * Chrome on Android has `BarcodeDetector` natively and it's fast and free. Safari does not
 * implement it, and because every iOS browser is WebKit underneath, *no* browser on iPhone
 * has it — so the WebAssembly decoder isn't a nicety, it's the iPhone code path.
 *
 * The wasm binary is imported through Vite (`?url`) rather than fetched from a CDN, so the
 * app keeps working offline and pulls nothing from a third-party host at runtime.
 */

import wasmUrl from 'zxing-wasm/reader/zxing_reader.wasm?url'

/** Retail formats only. Narrowing the set makes decoding faster and cuts false positives. */
const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e'] as const

export type DecoderKind = 'native' | 'wasm'

export interface Decoder {
  kind: DecoderKind
  detect(source: HTMLVideoElement): Promise<string[]>
  close(): void
}

interface BarcodeDetectorLike {
  detect(source: CanvasImageSource): Promise<{ rawValue: string }[]>
}

type BarcodeDetectorCtor = new (options?: { formats?: readonly string[] }) => BarcodeDetectorLike

function nativeCtor(): BarcodeDetectorCtor | null {
  const ctor = (globalThis as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector
  return typeof ctor === 'function' ? ctor : null
}

export function hasNativeDetector(): boolean {
  return nativeCtor() !== null
}

class NativeDecoder implements Decoder {
  kind: DecoderKind = 'native'
  private detector: BarcodeDetectorLike

  constructor(ctor: BarcodeDetectorCtor) {
    this.detector = new ctor({ formats: FORMATS })
  }

  async detect(video: HTMLVideoElement): Promise<string[]> {
    const found = await this.detector.detect(video)
    return found.map((b) => b.rawValue)
  }

  close(): void {}
}

class WasmDecoder implements Decoder {
  kind: DecoderKind = 'wasm'
  private canvas = document.createElement('canvas')
  private ctx = this.canvas.getContext('2d', { willReadFrequently: true })
  private read: typeof import('zxing-wasm/reader').readBarcodes | null = null

  async detect(video: HTMLVideoElement): Promise<string[]> {
    if (!this.ctx || !video.videoWidth) return []

    if (!this.read) {
      // Loaded lazily: on Android this module is never fetched at all.
      const mod = await import('zxing-wasm/reader')
      mod.prepareZXingModule({ overrides: { locateFile: () => wasmUrl }, fireImmediately: true })
      this.read = mod.readBarcodes
    }

    // Downscale to ~640px wide. A 1080p frame costs several times more to decode with no
    // gain — a barcode held at reading distance is comfortably resolved at this size.
    const scale = Math.min(1, 640 / video.videoWidth)
    const w = Math.round(video.videoWidth * scale)
    const h = Math.round(video.videoHeight * scale)
    if (this.canvas.width !== w || this.canvas.height !== h) {
      this.canvas.width = w
      this.canvas.height = h
    }
    this.ctx.drawImage(video, 0, 0, w, h)

    const results = await this.read(this.ctx.getImageData(0, 0, w, h), {
      formats: ['EAN-13', 'EAN-8', 'UPC-A', 'UPC-E'],
      maxNumberOfSymbols: 1,
    })
    return results.filter((r) => r.isValid !== false).map((r) => r.text)
  }

  close(): void {
    this.canvas.width = 0
    this.canvas.height = 0
  }
}

export function createDecoder(): Decoder {
  const ctor = nativeCtor()
  return ctor ? new NativeDecoder(ctor) : new WasmDecoder()
}

/**
 * A short ascending beep on a hit, a lower one on an unknown code.
 *
 * Audio feedback is what makes continuous scanning usable: you can keep your eyes on the
 * shopping instead of on the screen, and still know whether a code registered.
 */
let audioCtx: AudioContext | null = null

export function beep(kind: 'hit' | 'miss' = 'hit'): void {
  try {
    audioCtx ??= new AudioContext()
    // iOS suspends the context until a user gesture; scanning always follows a tap.
    if (audioCtx.state === 'suspended') void audioCtx.resume()
    const osc = audioCtx.createOscillator()
    const gain = audioCtx.createGain()
    osc.frequency.value = kind === 'hit' ? 880 : 320
    gain.gain.setValueAtTime(0.06, audioCtx.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.14)
    osc.connect(gain).connect(audioCtx.destination)
    osc.start()
    osc.stop(audioCtx.currentTime + 0.15)
  } catch {
    /* audio is a convenience — never let it break scanning */
  }
}

/** `getUserMedia` needs a secure context: https, or localhost. Plain http on a LAN IP
 *  silently refuses, which is the single most likely reason the camera won't open. */
export function cameraAvailable(): boolean {
  return Boolean(navigator.mediaDevices?.getUserMedia) && window.isSecureContext
}
