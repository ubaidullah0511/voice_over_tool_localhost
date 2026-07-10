I'm restructuring deployment for Voice Clone Studio:
- Frontend: deploy to Vercel (separate from the backend now)
- Backend: stays on my existing RunPod Pod, but should be STOPPED by default 
  and only started when a user clicks "Generate" — auto-stop after idle time

## Part 1: Wake/proxy layer (Vercel serverless function)

Create a Vercel API route (e.g. api/wake.ts) that:
1. Checks the RunPod Pod's current status via RunPod's GraphQL/REST API 
   (use RUNPOD_API_KEY and RUNPOD_POD_ID as Vercel env vars, server-side only)
2. If stopped, calls RunPod's resume/start pod endpoint
3. Polls the backend's /api/health endpoint (via the pod's proxy URL) every 
   few seconds until it returns model_loaded: true, with a reasonable timeout 
   (~2 minutes)
4. Returns a status the frontend can poll: "starting" | "ready" | "error"

## Part 2: Frontend changes

- Before submitting a generation request, call /api/wake first
- Show a clear loadingstate ("Warming up the voice model...") while waking, 
  not just a spinner -- this can take 30-90+ seconds
- Once wake returns "ready", proceed with the actual generation request 
  directly to the RunPod backend URL
- Update all API calls in api.ts to point at the RunPod backend's proxy 
  domain as the base URL (not relative paths, since frontend and backend 
  are now different origins)
- Attach Clerk JWT as before via authFetch

## Part 3: Backend changes (idle auto-stop)

- Add a background task that tracks the timestamp of the last received 
  request (any authenticated endpoint)
- Every N minutes (configurable, default 10), check: if idle longer than 
  threshold, call RunPod's API to stop this Pod (using RUNPOD_API_KEY and 
  RUNPOD_POD_ID as backend env vars)
- Make sure in-flight jobs are not interrupted -- don't self-stop if the 
  queue has active/pending jobs, only when genuinely idle

## Part 4: CORS

- Update backend/.env ALLOWED_ORIGINS to include the actual Vercel deployment 
  domain (I'll provide this after deploying to Vercel)
- Confirm CORS middleware is actually active now (it was dead code before 
  when frontend/backend were same-origin -- now it's load-bearing again)

## Part 5: Vercel deployment

- Set up frontend/ as a standalone Vercel project (vite build, standard 
  static output)
- Document the required Vercel env vars: VITE_CLERK_PUBLISHABLE_KEY, 
  RUNPOD_API_KEY, RUNPOD_POD_ID, and the backend's base proxy URL
- Confirm vite.config.ts doesn't still assume same-origin /api proxying 
  from before -- remove that if present

## Part 6: Preset audio upload limit change

Find the current reference audio upload duration limit (currently 15 seconds) 
used when creating a voice preset, and change it to 60 seconds. This likely 
touches:
- Frontend validation (wherever the file/duration check happens before upload, 
  probably near the preset creation form)
- Backend validation if duration is also checked server-side on the 
  /api/presets endpoint or during audio processing
- Any user-facing copy/tooltips that mention the old "15 second" limit

Confirm both frontend and backend enforce the same new limit consistently -- 
don't leave one at 15s and the other at 60s.

## Constraints
- Don't expose RUNPOD_API_KEY to client-side code anywhere -- it must only 
  live in the Vercel serverless function's server-side env and the backend's 
  own env, never shipped in the frontend bundle
- Show me the wake flow's timeout/error handling before considering this done 
  -- what does the user see if the pod fails to start within the timeout?
- Confirm current in-flight generation jobs block auto-stop
- Walk me through exactly what env vars I need to set where (Vercel dashboard 
  vs backend .env) before this is testable
- Show me every place the "15 second" limit was found before changing it, 
  so I can confirm nothing was missed
