import './LandingPage.css'

interface Props {
  onEnter: () => void
}

export default function LandingPage({ onEnter }: Props) {
  return (
    <div className="landing">
      <header className="landing-header">
        <div className="landing-container landing-header-content">
          <span className="landing-logo">
            <i className="ti ti-microphone" />
            Voice Clone Studio
          </span>
          <nav className="landing-nav">
            <a href="#features">Features</a>
            <a href="#workflow">How it works</a>
            <a href="#specs">Specs</a>
          </nav>
        </div>
      </header>

      <section className="landing-hero">
        <div className="landing-container">
          <div className="landing-hero-badge">
            <i className="ti ti-bolt" /> Fast, local, on-device
          </div>
          <h1>Clone any voice in seconds</h1>
          <p className="landing-subtitle">
            Create perfect AI voice clones with a 2-60 second reference clip. Studio-quality
            voiceovers at scale, all running locally on your GPU.
          </p>
          <div className="landing-cta-buttons">
            <button type="button" className="landing-btn landing-btn-primary" onClick={onEnter}>
              <i className="ti ti-player-play" /> Try now
            </button>
            <a href="#workflow" className="landing-btn landing-btn-secondary">
              <i className="ti ti-book" /> Read the docs
            </a>
          </div>
        </div>
      </section>

      <section className="landing-features" id="features">
        <div className="landing-container">
          <h2 className="landing-section-title">What you can do</h2>
          <div className="landing-features-grid">
            <div className="landing-card">
              <div className="landing-card-icon">
                <i className="ti ti-wave-square" />
              </div>
              <h3>Instant voice clones</h3>
              <p>
                Upload a 2-60 second reference clip and get a perfect voice clone.
                Auto-transcription keeps everything simple.
              </p>
            </div>
            <div className="landing-card">
              <div className="landing-card-icon">
                <i className="ti ti-stack-2" />
              </div>
              <h3>Batch generation</h3>
              <p>
                Write multiple scripts and assign different voices in one sitting. Queue them
                all at once and watch them process.
              </p>
            </div>
            <div className="landing-card">
              <div className="landing-card-icon">
                <i className="ti ti-settings" />
              </div>
              <h3>Style & control</h3>
              <p>
                Adjust stability and style per batch. Cinematic, conversational, dramatic --
                dial in the mood you need.
              </p>
            </div>
            <div className="landing-card">
              <div className="landing-card-icon">
                <i className="ti ti-history" />
              </div>
              <h3>Full history</h3>
              <p>
                Every generation is saved. Re-queue with edits in one click and regenerate
                without starting from scratch.
              </p>
            </div>
            <div className="landing-card">
              <div className="landing-card-icon">
                <i className="ti ti-server" />
              </div>
              <h3>Local & fast</h3>
              <p>
                Everything runs on your GPU. No cloud calls, no data leaving your machine, no
                subscription tiers.
              </p>
            </div>
            <div className="landing-card">
              <div className="landing-card-icon">
                <i className="ti ti-files" />
              </div>
              <h3>Long scripts</h3>
              <p>
                Scripts up to 60,000 characters automatically chunked. No audio degradation, no
                noise. Just clean output.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="landing-workflow" id="workflow">
        <div className="landing-container">
          <h2 className="landing-section-title">How it works</h2>
          <div className="landing-steps">
            <div className="landing-step">
              <div className="landing-step-number">1</div>
              <h3>Add a voice</h3>
              <p>
                Create a preset with a reference clip (2-60 sec). Name it, optionally add a
                mood tag. Let it auto-transcribe or paste the exact text.
              </p>
            </div>
            <div className="landing-step">
              <div className="landing-step-number">2</div>
              <h3>Write scripts</h3>
              <p>
                Write voiceover text in script blocks. Assign a voice to each one. Add as many
                as you want -- each gets its own voice dropdown.
              </p>
            </div>
            <div className="landing-step">
              <div className="landing-step-number">3</div>
              <h3>Generate all</h3>
              <p>
                Click "Generate All" to queue every script at once. The app switches to Queue &
                History so you can watch them process.
              </p>
            </div>
            <div className="landing-step">
              <div className="landing-step-number">4</div>
              <h3>Monitor queue</h3>
              <p>
                Jobs run one at a time. See queued status, live progress, and estimated time
                remaining. Downloads ready instantly when done.
              </p>
            </div>
            <div className="landing-step">
              <div className="landing-step-number">5</div>
              <h3>Edit & re-queue</h3>
              <p>
                Re-queue with one click. Pull any finished job back into the studio and
                regenerate. Save your best takes.
              </p>
            </div>
          </div>
        </div>
      </section>

      <section className="landing-specs" id="specs">
        <div className="landing-container">
          <h2 className="landing-section-title">Specs & limits</h2>
          <div className="landing-specs-grid">
            <div className="landing-spec-box">
              <div className="landing-spec-value">2-60s</div>
              <div className="landing-spec-label">Reference clip</div>
            </div>
            <div className="landing-spec-box">
              <div className="landing-spec-value">60k</div>
              <div className="landing-spec-label">Max characters per script</div>
            </div>
            <div className="landing-spec-box">
              <div className="landing-spec-value">~85s</div>
              <div className="landing-spec-label">Per chunk on single GPU</div>
            </div>
            <div className="landing-spec-box">
              <div className="landing-spec-value">∞</div>
              <div className="landing-spec-label">Saved presets</div>
            </div>
            <div className="landing-spec-box">
              <div className="landing-spec-value">Chunked</div>
              <div className="landing-spec-label">Long-form support</div>
            </div>
            <div className="landing-spec-box">
              <div className="landing-spec-value">Auto</div>
              <div className="landing-spec-label">Transcription</div>
            </div>
          </div>
        </div>
      </section>

      <div className="landing-cta-buttons" style={{ paddingBottom: 40 }}>
        <button type="button" className="landing-btn landing-btn-primary" onClick={onEnter}>
          <i className="ti ti-player-play" /> Enter Studio
        </button>
      </div>

      <footer className="landing-footer">
        <div className="landing-container">
          <p>Voice Clone Studio -- run locally, generate at scale</p>
        </div>
      </footer>
    </div>
  )
}
