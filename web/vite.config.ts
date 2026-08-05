import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

/**
 * TLS certs for LAN access, if `make dev-https` has generated them.
 *
 * `getUserMedia` only works in a secure context. `localhost` counts; `http://192.168.x.x`
 * does not — the browser refuses without any visible error, which makes it look like the
 * scanner is broken. Since the scanner is only useful on a phone, HTTPS on the LAN is a
 * requirement rather than a nicety.
 */
function localHttps() {
  const dir = resolve(__dirname, '.certs')
  const key = resolve(dir, 'key.pem')
  const cert = resolve(dir, 'cert.pem')
  if (!existsSync(key) || !existsSync(cert)) return undefined
  return { key: readFileSync(key), cert: readFileSync(cert) }
}

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Bind on all interfaces so a phone on the same Wi-Fi can reach the dev server.
    host: true,
    https: localHttps(),
    // Proxy keeps the browser same-origin in dev, so auth headers behave the same way
    // they will in production behind a single host.
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
