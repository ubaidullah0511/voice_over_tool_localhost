import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// No dev-server proxy anymore -- frontend and backend are separate origins
// (Vercel + the RunPod pod's proxy domain), and every API/media call in
// src/api.ts already goes through an absolute VITE_BACKEND_URL instead of a
// relative path, so a same-origin proxy would just be dead config. For local
// dev, set VITE_BACKEND_URL in frontend/.env.local to wherever your backend
// is actually running (e.g. http://127.0.0.1:8000).
export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // bind to all network interfaces, not just localhost -- lets you reach the dev server via the machine's LAN IP (e.g. from another device)
  },
})
