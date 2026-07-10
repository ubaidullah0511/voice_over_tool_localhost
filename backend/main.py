import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root, parent of the vendored `qwen` package

import numpy as np
import requests
import torch
import soundfile as sf
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

import credits
from auth import get_current_user, get_last_activity
from config.plans import DEFAULT_PLAN, PLANS
from qwen import FasterQwen3TTS
from audio_convert import wav_to_mp4, write_mp4
from audio_stitcher import stitch_audio
from text_chunker import chunk_text

_whisper_model = None
_whisper_lock = threading.Lock()


def _transcribe_audio(path: str) -> str:
    """Auto-transcribe a reference clip with faster-whisper (CPU, so it doesn't
    contend with the TTS model for this machine's 4GB of VRAM)."""
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel
            _whisper_model = WhisperModel("base", device="cpu", compute_type="int8")
        segments, _ = _whisper_model.transcribe(path)
        return " ".join(seg.text.strip() for seg in segments).strip()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_clone_studio")

# Overridable via MODEL_PATH in backend/.env -- the default below only holds
# on the original dev machine's local model cache. Any other host (including
# a RunPod pod) must set MODEL_PATH to wherever it downloaded the snapshot.
MODEL_PATH = os.environ.get(
    "MODEL_PATH",
    r"D:\models_cache\models--Qwen--Qwen3-TTS-12Hz-0.6B-Base\snapshots\5d83992436eae1d760afd27aff78a71d676296fc",
)

STORAGE_DIR = Path(__file__).parent / "storage"
REF_DIR = STORAGE_DIR / "references"
GEN_DIR = STORAGE_DIR / "generated"
PRESETS_FILE = STORAGE_DIR / "presets.json"
HISTORY_FILE = STORAGE_DIR / "history.json"
QUEUE_FILE = STORAGE_DIR / "queue.json"
REF_DIR.mkdir(parents=True, exist_ok=True)
GEN_DIR.mkdir(parents=True, exist_ok=True)

# Per-chunk char budget so a single chunk's prefill + decode stay within
# max_seq_len=1024 (tuned for this machine's 4GB GPU -- see
# ../qwen/HOW_TO_RUN.md). Empirically tested: 800 chars / 700 max_new_tokens
# with a short (~3.5s) reference clip takes ~85s and comfortably fits. Longer
# reference clips (10+s) eat into the same max_seq_len budget and will be much
# slower or may exceed it -- the talker's own StaticCache bounds check is the
# final safety net (surfaced as a chunk failure in the job's "error" field).
#
# Long scripts are handled by splitting into multiple CHUNK_MAX_CHARS-sized
# pieces (text_chunker.chunk_text), each generated independently with a fresh
# KV cache -- this is what keeps quality stable past the point where a single
# long generation would drift into noise as cache position approaches
# max_seq_len (rope embeddings extrapolating past the range this model/config
# was validated for). See audio_stitcher.stitch_audio for how the per-chunk
# audio is recombined.
CHUNK_MAX_CHARS = 800
MAX_NEW_TOKENS = 700
MAX_TOTAL_CHARS = 60_000
STITCH_GAP_SECONDS = 0.2

# Idle auto-stop: RUNPOD_API_KEY/RUNPOD_POD_ID let this process stop its own
# RunPod pod once nobody's using it (paired with the Vercel api/wake.ts
# function on the frontend, which resumes it on demand). Left unset for local
# dev, where the loop below just logs once and never runs.
RUNPOD_API_KEY = os.environ.get("RUNPOD_API_KEY")
RUNPOD_POD_ID = os.environ.get("RUNPOD_POD_ID")
IDLE_CHECK_INTERVAL_MIN = float(os.environ.get("IDLE_CHECK_INTERVAL_MIN", "10"))
IDLE_STOP_THRESHOLD_MIN = float(os.environ.get("IDLE_STOP_THRESHOLD_MIN", "10"))

# Reference-audio duration bounds for new presets. ICL voice cloning gets
# unstable outside this range: too short starves the speaker encoder of
# signal; too long eats into the same max_seq_len budget generation uses and
# has been observed (empirically, this session) to cause unstable output --
# degenerate babbling that runs to the full token budget, or near-instant
# stopping -- regardless of chunk size. 23s reference clips reproduced this
# reliably at the old 15s cap. Raised to 60s by product decision (2026-07) --
# this is well past the 23s point where instability was previously observed,
# so watch for garbled/looping output on long reference clips and lower this
# again if it reproduces.
MIN_REF_AUDIO_SECS = 2.0
MAX_REF_AUDIO_SECS = 60.0
# Loose sanity check that ref_text is plausibly a transcript of ref audio,
# not a placeholder (e.g. "ZAZA" for a 23s clip). Real speech is roughly
# 12-15 chars/sec; anything under ~3 chars/sec is almost certainly wrong.
MIN_REF_TEXT_CHARS_PER_SEC = 3.0

# Time estimation: rolling average of chars/second from the last N completed
# jobs (seeded from history.json's persisted generation_s on startup so
# estimates are sane immediately after a restart, not just after the first
# job). Falls back to the empirically-established CHUNK_MAX_CHARS/85s baseline
# (~9.4 chars/sec) until enough real samples exist.
TIMING_WINDOW = 20
_FALLBACK_CHARS_PER_SEC = CHUNK_MAX_CHARS / 85.0

STYLE_INSTRUCTIONS = {
    "natural": None,
    "clear": "Speak clearly and plainly, enunciating each word.",
    "expressive": "Speak expressively, with varied and lively intonation.",
    "dramatic": "Speak dramatically, with strong emotional emphasis.",
}

STABILITY_PARAMS = {
    "stable": dict(temperature=0.5, top_p=0.85, top_k=30, do_sample=True),
    "balanced": dict(temperature=0.9, top_p=1.0, top_k=50, do_sample=True),
    "creative": dict(temperature=1.2, top_p=1.0, top_k=80, do_sample=True),
}

_tts: Optional[FasterQwen3TTS] = None
_gen_lock = threading.Lock()
_store_lock = threading.Lock()

# Job store + FIFO queue. A single dedicated worker thread processes
# _pending_job_ids in order -- this matches the single-GPU reality (the
# CUDA-graphed talker/predictor can only run one generation at a time
# regardless of how many threads you throw at it) rather than pretending to
# support concurrency the hardware can't back up.
_jobs_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_pending_job_ids: list[str] = []
_current_running_job_id: Optional[str] = None
_queue_event = threading.Event()

_timing_lock = threading.Lock()
_timing_samples: list[float] = []  # chars/second, most-recent-last, capped at TIMING_WINDOW


def _load_json(path: Path) -> list:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: Path, data: list) -> None:
    # File lock (not just the in-process _store_lock) so concurrent writers
    # across processes -- e.g. a second backend instance started by mistake --
    # can't interleave writes to the same file.
    lock = FileLock(str(path) + ".lock", timeout=10)
    with lock:
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)


_presets: list[dict] = _load_json(PRESETS_FILE)  # newest first
_history: list[dict] = _load_json(HISTORY_FILE)  # newest first


def _find_preset(preset_id: str) -> Optional[dict]:
    return next((p for p in _presets if p["id"] == preset_id), None)


# ---- Timing estimation ----------------------------------------------------

def _seed_timing_from_history() -> None:
    samples = []
    for entry in _history:  # newest first
        gen_s = entry.get("generation_s")
        text = entry.get("text", "")
        if gen_s and gen_s > 0 and text:
            samples.append(len(text) / gen_s)
        if len(samples) >= TIMING_WINDOW:
            break
    with _timing_lock:
        _timing_samples.extend(reversed(samples))  # oldest-of-the-seed-batch first


def _avg_chars_per_second() -> float:
    with _timing_lock:
        if not _timing_samples:
            return _FALLBACK_CHARS_PER_SEC
        return sum(_timing_samples) / len(_timing_samples)


def _record_timing_sample(char_count: int, generation_s: float) -> None:
    if generation_s <= 0 or char_count <= 0:
        return
    with _timing_lock:
        _timing_samples.append(char_count / generation_s)
        if len(_timing_samples) > TIMING_WINDOW:
            _timing_samples.pop(0)


def _estimate_seconds(char_count: int) -> float:
    rate = _avg_chars_per_second()
    return char_count / rate if rate > 0 else char_count / _FALLBACK_CHARS_PER_SEC


# ---- Queue helpers (all assume caller holds _jobs_lock) --------------------

def _queue_position_locked(job_id: str) -> Optional[int]:
    try:
        return _pending_job_ids.index(job_id)
    except ValueError:
        return None


def _job_elapsed_seconds_locked(job_id: str) -> Optional[float]:
    job = _jobs[job_id]
    started = job.get("started_at")
    if started is None:
        return None
    end = job.get("finished_at") or time.time()
    return end - started


def _job_eta_seconds_locked(job_id: str) -> Optional[float]:
    job = _jobs[job_id]
    status = job["status"]
    if status in ("done", "error", "canceled"):
        return None

    total = job["total_chunks"] or 1
    done = job["chunks_done"]

    if status == "running":
        elapsed = _job_elapsed_seconds_locked(job_id) or 0.0
        per_chunk = (elapsed / done) if done > 0 else (job["estimated_s"] / total)
        return max(per_chunk * (total - done), 0.0)

    # queued: wait for the running job to finish + every queued job ahead of this one
    wait = 0.0
    if _current_running_job_id is not None:
        wait += _job_eta_seconds_locked(_current_running_job_id) or 0.0
    for jid in _pending_job_ids:
        if jid == job_id:
            break
        wait += _jobs[jid]["estimated_s"]
    return wait + job["estimated_s"]


def _persist_queue_locked() -> None:
    """Persist enough to rebuild the queue (queued + in-flight jobs) after a
    restart. Completed/errored/canceled jobs aren't persisted here -- they
    either already landed in history.json or don't need resuming."""
    to_persist = []
    if _current_running_job_id is not None:
        to_persist.append(_current_running_job_id)
    to_persist.extend(_pending_job_ids)

    records = []
    for job_id in to_persist:
        job = _jobs[job_id]
        records.append({
            "job_id": job_id,
            "user_id": job["user_id"],
            "preset_id": job["preset_id"],
            "text": job["text"],
            "language": job["language"],
            "style": job["style"],
            "stability": job["stability"],
            "submitted_at": job["submitted_at"],
            "estimated_s": job["estimated_s"],
        })
    _save_json(QUEUE_FILE, records)


def _enqueue_job_locked(job_id: str, job: dict) -> None:
    _jobs[job_id] = job
    _pending_job_ids.append(job_id)
    _persist_queue_locked()
    _queue_event.set()


def _restore_queue_on_startup() -> None:
    records = _load_json(QUEUE_FILE)
    if not records:
        return
    restored = 0
    with _jobs_lock:
        for record in records:
            preset = _find_preset(record["preset_id"])
            if preset is None:
                logger.warning(
                    "Skipping queued job %s on restore -- preset %s no longer exists",
                    record["job_id"], record["preset_id"],
                )
                continue
            text = record["text"]
            chunks = chunk_text(text, CHUNK_MAX_CHARS)
            job_id = record["job_id"]
            _jobs[job_id] = {
                # .get(), not [] -- queue.json written before the multiuser
                # migration won't have this key. Such orphaned jobs just won't
                # surface in any user's queue until migrate_to_multiuser.py runs.
                "user_id": record.get("user_id"),
                "status": "queued",
                "preset_id": preset["id"],
                "preset_name": preset["name"],
                "text": text,
                "language": record["language"],
                "style": record["style"],
                "stability": record["stability"],
                "chunks": chunks,
                "chunks_done": 0,
                "total_chunks": len(chunks),
                "audio_url": None,
                "sample_rate": None,
                "error": None,
                "submitted_at": record["submitted_at"],
                "started_at": None,
                "finished_at": None,
                "estimated_s": record["estimated_s"],
            }
            _pending_job_ids.append(job_id)
            restored += 1
        if restored:
            _persist_queue_locked()
    if restored:
        logger.info("Restored %d queued job(s) from queue.json", restored)


# ---- Worker -----------------------------------------------------------------

def _process_job(job_id: str) -> None:
    global _current_running_job_id
    with _jobs_lock:
        job = _jobs[job_id]
        job["status"] = "running"
        job["started_at"] = time.time()
        _current_running_job_id = job_id
        _persist_queue_locked()

    preset = {"id": job["preset_id"], "name": job["preset_name"]}
    # audio_path/ref_text aren't stored on the job dict (only preset_id/name are,
    # to keep persisted queue records small) -- look the live preset up fresh so
    # edits to ref_text/audio between submission and processing take effect.
    live_preset = _find_preset(job["preset_id"])
    if live_preset is None:
        with _jobs_lock:
            job.update(status="error", error="Preset was deleted before this job could run.")
            _current_running_job_id = None
            _persist_queue_locked()
        credits.release_reservation(job["user_id"])
        return
    preset = live_preset

    chunks = job["chunks"]
    language = job["language"]
    style = job["style"]
    stability = job["stability"]
    text = job["text"]

    logger.info(
        "Job %s: starting -- preset=%r chunks=%d style=%s stability=%s",
        job_id, preset["name"], len(chunks), style, stability,
    )

    audio_chunks: list[np.ndarray] = []
    sr: Optional[int] = None

    for i, chunk in enumerate(chunks):
        last_error: Optional[Exception] = None
        audio_arrays = None
        for attempt in range(2):  # one retry per chunk before giving up
            try:
                with _gen_lock:
                    audio_arrays, sr = _tts.generate_voice_clone(
                        text=chunk,
                        language=language,
                        ref_audio=preset["audio_path"],
                        ref_text=preset["ref_text"],
                        instruct=STYLE_INSTRUCTIONS[style],
                        max_new_tokens=MAX_NEW_TOKENS,
                        **STABILITY_PARAMS[stability],
                    )
                last_error = None
                break
            except RuntimeError as e:
                last_error = e
                logger.exception(
                    "Job %s: chunk %d/%d attempt %d failed", job_id, i + 1, len(chunks), attempt + 1,
                )
                # A CUDA-level error (e.g. Windows TDR killing a kernel) leaves the
                # process's CUDA context unusable -- retrying in the same process
                # would just fail again. Fail fast instead of wasting a retry.
                if "CUDA error" in str(e):
                    break

        if last_error is not None:
            error_msg = f"Chunk {i + 1}/{len(chunks)} failed: {last_error}"
            if "CUDA error" in str(last_error):
                error_msg += " -- GPU driver reset; restart the backend process before retrying."
            with _jobs_lock:
                job.update(status="error", error=error_msg, finished_at=time.time())
                _current_running_job_id = None
                _persist_queue_locked()
            credits.release_reservation(job["user_id"])
            return

        audio_chunks.append(audio_arrays[0])
        with _jobs_lock:
            job["chunks_done"] = i + 1
        logger.info("Job %s: chunk %d/%d done", job_id, i + 1, len(chunks))

    final_audio = stitch_audio(audio_chunks, sr, gap_seconds=STITCH_GAP_SECONDS)
    out_name = f"{uuid.uuid4().hex}.mp4"
    write_mp4(final_audio, sr, str(GEN_DIR / out_name))
    audio_url = f"/audio/{out_name}"
    output_duration_s = len(final_audio) / sr
    finished_at = time.time()
    generation_s = finished_at - job["started_at"]
    logger.info(
        "Job %s: done -- %s (%.1fs audio, %.1fs generation time)",
        job_id, audio_url, output_duration_s, generation_s,
    )

    _record_timing_sample(len(text), generation_s)

    entry = {
        "id": uuid.uuid4().hex,
        "user_id": job["user_id"],
        "preset_id": preset["id"],
        "preset_name": preset["name"],
        "text": text,
        "language": language,
        "style": style,
        "stability": stability,
        "audio_url": audio_url,
        "duration_s": output_duration_s,
        "generation_s": generation_s,
        "estimated_s": job["estimated_s"],
        "created_at": time.time(),
    }
    with _store_lock:
        _history.insert(0, entry)
        _save_json(HISTORY_FILE, _history)

    with _jobs_lock:
        job.update(status="done", audio_url=audio_url, sample_rate=sr, finished_at=finished_at)
        _current_running_job_id = None
        _persist_queue_locked()
    credits.consume_reservation(job["user_id"])


def _worker_loop() -> None:
    while True:
        with _jobs_lock:
            job_id = _pending_job_ids.pop(0) if _pending_job_ids else None
            if job_id is None:
                _queue_event.clear()
        if job_id is None:
            _queue_event.wait(timeout=1.0)
            continue
        try:
            _process_job(job_id)
        except Exception:
            logger.exception("Job %s: worker crashed unexpectedly", job_id)
            with _jobs_lock:
                _jobs[job_id].update(
                    status="error", error="Internal error -- see backend logs.", finished_at=time.time(),
                )
                global _current_running_job_id
                _current_running_job_id = None
                _persist_queue_locked()
            credits.release_reservation(_jobs[job_id]["user_id"])


def _stop_runpod_pod() -> bool:
    try:
        resp = requests.post(
            f"https://rest.runpod.io/v1/pods/{RUNPOD_POD_ID}/stop",
            headers={"Authorization": f"Bearer {RUNPOD_API_KEY}"},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Idle auto-stop: RunPod pod %s stop requested.", RUNPOD_POD_ID)
        return True
    except Exception:
        logger.exception("Idle auto-stop: failed to stop RunPod pod %s -- will retry next check", RUNPOD_POD_ID)
        return False


def _idle_stop_loop() -> None:
    """Stops this pod once it's been idle (no authenticated request) for
    IDLE_STOP_THRESHOLD_MIN minutes, checked every IDLE_CHECK_INTERVAL_MIN
    minutes. Never fires while a job is running or queued -- an in-flight
    generation must never be interrupted by a self-stop, even if it happens
    to run long past the idle threshold with no new requests coming in."""
    if not RUNPOD_API_KEY or not RUNPOD_POD_ID:
        logger.info(
            "RUNPOD_API_KEY/RUNPOD_POD_ID not set -- idle auto-stop disabled "
            "(expected for local dev)."
        )
        return
    logger.info(
        "Idle auto-stop enabled: checking every %.0fm, stopping after %.0fm with no "
        "authenticated requests and an empty job queue.",
        IDLE_CHECK_INTERVAL_MIN, IDLE_STOP_THRESHOLD_MIN,
    )
    while True:
        time.sleep(IDLE_CHECK_INTERVAL_MIN * 60)
        with _jobs_lock:
            busy = _current_running_job_id is not None or bool(_pending_job_ids)
        if busy:
            logger.info("Idle auto-stop: queue has active/pending jobs -- skipping check.")
            continue
        idle_for = time.time() - get_last_activity()
        if idle_for < IDLE_STOP_THRESHOLD_MIN * 60:
            continue
        logger.info(
            "Idle auto-stop: idle for %.0fm with an empty queue -- stopping pod.",
            idle_for / 60,
        )
        if _stop_runpod_pod():
            return  # pod is stopping -- nothing left to check
        # else: stop call failed (transient RunPod API error) -- loop back
        # and retry at the next check interval instead of giving up forever.


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _tts
    _seed_timing_from_history()
    _tts = FasterQwen3TTS.from_pretrained(
        MODEL_PATH,
        device="cuda",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        max_seq_len=1024,
    )
    _restore_queue_on_startup()
    threading.Thread(target=_worker_loop, daemon=True).start()
    threading.Thread(target=_idle_stop_loop, daemon=True).start()
    yield


# Overridable via ALLOWED_ORIGINS in backend/.env (comma-separated) -- the
# localhost default only covers the Vite dev server on the same machine.
# A RunPod (or any other) deployment reached through a different origin --
# e.g. a proxied *.proxy.runpod.net domain -- must add that origin here or
# the browser will block the frontend's authenticated API calls.
ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o.strip()
]

app = FastAPI(title="CloneVoicePrompt-style TTS API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/audio", StaticFiles(directory=str(GEN_DIR)), name="audio")
app.mount("/refs", StaticFiles(directory=str(REF_DIR)), name="refs")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return cleaned or "voice_clone"


@app.get("/api/download/{filename}")
def download_audio(filename: str, name: str = "voice_clone"):
    """Serve a generated clip as a renamed .mp4 download. New generations are
    written as .mp4 directly (see write_mp4 in _process_job) and are served
    as-is here. History entries from before that change still point at an
    on-disk .wav -- those get converted (via PyAV) and cached on first hit."""
    src_path = (GEN_DIR / filename).resolve()
    if (
        GEN_DIR.resolve() not in src_path.parents
        or src_path.suffix.lower() not in (".mp4", ".wav")
        or not src_path.exists()
    ):
        raise HTTPException(404, "Unknown audio file")

    if src_path.suffix.lower() == ".mp4":
        mp4_path = src_path
    else:
        mp4_path = src_path.with_suffix(".mp4")
        if not mp4_path.exists():
            try:
                wav_to_mp4(str(src_path), str(mp4_path))
            except Exception as e:
                mp4_path.unlink(missing_ok=True)
                logger.exception("Failed to convert %s to mp4", src_path)
                raise HTTPException(500, f"Could not convert audio to mp4: {e}")

    return FileResponse(
        str(mp4_path), media_type="audio/mp4", filename=f"{_safe_filename(name)}.mp4",
    )


@app.get("/api/health")
def health():
    return {"model_loaded": _tts is not None, "sample_rate": _tts.sample_rate if _tts else None}


@app.get("/api/languages")
def languages():
    if _tts is None:
        raise HTTPException(503, "Model not loaded yet")
    codec_language_id = _tts.model.model.config.talker_config.codec_language_id
    return {"languages": sorted(lang.capitalize() for lang in codec_language_id.keys())}


@app.get("/api/estimate")
def estimate(chars: int) -> dict:
    """Lightweight estimate for a given character count, using the same
    rolling average as job submission -- lets the frontend show a live
    estimate while the user is still typing, without spamming /api/generate."""
    return {"estimated_s": _estimate_seconds(max(chars, 0))}


@app.get("/api/account")
def get_account(user_id: str = Depends(get_current_user)) -> dict:
    user = credits.get_user(user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    plan = PLANS.get(user["plan"], PLANS[DEFAULT_PLAN])
    return {
        "user_id": user["user_id"],
        "email": user.get("email"),
        "plan": user["plan"],
        "unlimited": plan["unlimited"],
        "credits_remaining": user["credits_remaining"],
        "credits_reserved": user["credits_reserved"],
        "credits_total": plan["credits_per_month"],
        "credits_reset_at": user["credits_reset_at"],
    }


def _preset_response(preset: dict) -> dict:
    """Add fields derivable/servable at read time without persisting them
    redundantly (preview_url is just the reference file exposed over HTTP)."""
    return {**preset, "preview_url": f"/refs/{Path(preset['audio_path']).name}"}


@app.get("/api/presets")
def list_presets(user_id: str = Depends(get_current_user)):
    return {"presets": [_preset_response(p) for p in _presets if p.get("user_id") == user_id]}


@app.post("/api/presets")
async def create_preset(
    audio: UploadFile = File(...),
    name: str = Form(...),
    ref_text: str = Form(""),
    language: str = Form("English"),
    tag: str = Form(""),
    user_id: str = Depends(get_current_user),
):
    name = name.strip()
    ref_text = ref_text.strip()
    tag = tag.strip()
    if not name:
        raise HTTPException(400, "name is required")

    preset_id = uuid.uuid4().hex
    ext = Path(audio.filename or "ref.wav").suffix or ".wav"
    dest = REF_DIR / f"{preset_id}{ext}"
    dest.write_bytes(await audio.read())

    try:
        duration_s = sf.info(str(dest)).duration
    except Exception as e:
        dest.unlink(missing_ok=True)
        logger.exception("Failed to read reference audio for preset %r", name)
        raise HTTPException(400, f"Could not read reference audio file: {e}")

    if duration_s < MIN_REF_AUDIO_SECS:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            400,
            f"Reference audio is {duration_s:.1f}s, too short (minimum {MIN_REF_AUDIO_SECS}s) "
            "for reliable voice cloning.",
        )
    if duration_s > MAX_REF_AUDIO_SECS:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            400,
            f"Reference audio is {duration_s:.1f}s, too long (maximum {MAX_REF_AUDIO_SECS}s) -- "
            "longer reference clips have been observed to produce unstable/garbled generation "
            "on this model. Trim to a shorter, clean clip.",
        )

    if not ref_text:
        try:
            logger.info("Auto-transcribing reference audio for preset %r with faster-whisper", name)
            ref_text = _transcribe_audio(str(dest))
        except Exception as e:
            dest.unlink(missing_ok=True)
            logger.exception("Auto-transcription failed for preset %r", name)
            raise HTTPException(400, f"Auto-transcription failed: {e}. Provide ref_text manually.")
        if not ref_text:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                400,
                "Auto-transcription produced empty text -- the clip may be silent or unclear. "
                "Provide ref_text manually.",
            )

    if len(ref_text) / duration_s < MIN_REF_TEXT_CHARS_PER_SEC:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            400,
            f"ref_text ({len(ref_text)} chars) looks too short to be an accurate transcript of "
            f"{duration_s:.1f}s of audio. ref_text must be the exact transcript of what's spoken "
            "in the reference clip -- a mismatched transcript causes unstable voice cloning.",
        )

    logger.info(
        "Creating preset %r: duration=%.1fs ref_text_len=%d language=%s",
        name, duration_s, len(ref_text), language,
    )

    preset = {
        "id": preset_id,
        "user_id": user_id,
        "name": name,
        "language": language,
        "ref_text": ref_text,
        "audio_path": str(dest),
        "tag": tag,
        "is_builtin": False,
        "created_at": time.time(),
    }
    with _store_lock:
        _presets.insert(0, preset)
        _save_json(PRESETS_FILE, _presets)
    return _preset_response(preset)


@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: str, user_id: str = Depends(get_current_user)):
    preset = _find_preset(preset_id)
    if preset is None or preset.get("user_id") != user_id:
        raise HTTPException(404, "Unknown preset_id")
    with _store_lock:
        _presets.remove(preset)
        _save_json(PRESETS_FILE, _presets)
    Path(preset["audio_path"]).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/history")
def list_history(user_id: str = Depends(get_current_user)):
    return {"history": [h for h in _history if h.get("user_id") == user_id]}


@app.delete("/api/history/{entry_id}")
def delete_history_entry(entry_id: str, user_id: str = Depends(get_current_user)):
    entry = next((h for h in _history if h["id"] == entry_id), None)
    if entry is None or entry.get("user_id") != user_id:
        raise HTTPException(404, "Unknown history entry_id")
    with _store_lock:
        _history.remove(entry)
        _save_json(HISTORY_FILE, _history)
    audio_url = entry.get("audio_url", "")
    if audio_url.startswith("/audio/"):
        wav_path = GEN_DIR / audio_url.removeprefix("/audio/")
        wav_path.unlink(missing_ok=True)
        wav_path.with_suffix(".mp4").unlink(missing_ok=True)
    return {"ok": True}


class GenerateRequest(BaseModel):
    preset_id: str
    text: str
    language: str = "English"
    style: str = "natural"
    stability: str = "balanced"


class GenerateJobStart(BaseModel):
    job_id: str
    total_chunks: int
    estimated_s: float
    queue_position: int


class JobStatusResponse(BaseModel):
    status: str  # "queued" | "running" | "done" | "error" | "canceled"
    chunks_done: int
    total_chunks: int
    audio_url: Optional[str] = None
    sample_rate: Optional[int] = None
    error: Optional[str] = None
    estimated_s: Optional[float] = None
    elapsed_s: Optional[float] = None
    eta_s: Optional[float] = None
    queue_position: Optional[int] = None


class QueueEntry(BaseModel):
    job_id: str
    preset_name: str
    text_preview: str
    status: str
    chunks_done: int
    total_chunks: int
    estimated_s: Optional[float] = None
    elapsed_s: Optional[float] = None
    eta_s: Optional[float] = None
    queue_position: Optional[int] = None
    submitted_at: float
    audio_url: Optional[str] = None
    error: Optional[str] = None


class ReorderRequest(BaseModel):
    job_ids: list[str]


def _job_status_response_locked(job_id: str) -> JobStatusResponse:
    job = _jobs[job_id]
    return JobStatusResponse(
        status=job["status"],
        chunks_done=job["chunks_done"],
        total_chunks=job["total_chunks"],
        audio_url=job.get("audio_url"),
        sample_rate=job.get("sample_rate"),
        error=job.get("error"),
        estimated_s=job.get("estimated_s"),
        elapsed_s=_job_elapsed_seconds_locked(job_id),
        eta_s=_job_eta_seconds_locked(job_id),
        queue_position=_queue_position_locked(job_id),
    )


def _queue_entry_locked(job_id: str) -> QueueEntry:
    job = _jobs[job_id]
    text = job["text"]
    return QueueEntry(
        job_id=job_id,
        preset_name=job["preset_name"],
        text_preview=(text[:80] + "...") if len(text) > 80 else text,
        status=job["status"],
        chunks_done=job["chunks_done"],
        total_chunks=job["total_chunks"],
        estimated_s=job.get("estimated_s"),
        elapsed_s=_job_elapsed_seconds_locked(job_id),
        eta_s=_job_eta_seconds_locked(job_id),
        queue_position=_queue_position_locked(job_id),
        submitted_at=job["submitted_at"],
        audio_url=job.get("audio_url"),
        error=job.get("error"),
    )


@app.post("/api/generate", status_code=202)
def generate(req: GenerateRequest, user_id: str = Depends(get_current_user)) -> GenerateJobStart:
    if _tts is None:
        raise HTTPException(503, "Model not loaded yet")
    preset = _find_preset(req.preset_id)
    if preset is None or preset.get("user_id") != user_id:
        raise HTTPException(404, "Unknown preset_id -- create a preset first")
    text = req.text.strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > MAX_TOTAL_CHARS:
        raise HTTPException(
            400,
            f"Script too long ({len(text)} > {MAX_TOTAL_CHARS} chars).",
        )
    style = req.style.lower()
    stability = req.stability.lower()
    if style not in STYLE_INSTRUCTIONS:
        raise HTTPException(400, f"Unknown style '{req.style}'")
    if stability not in STABILITY_PARAMS:
        raise HTTPException(400, f"Unknown stability '{req.stability}'")

    # Reserved at submit time (not on job start) so a burst of queued jobs
    # can't overrun a free-tier budget before any of them complete -- see
    # credits.reserve_credit for the atomicity guarantee under concurrent
    # requests.
    if not credits.reserve_credit(user_id):
        raise HTTPException(402, "No credits remaining for this billing period.")

    chunks = chunk_text(text, CHUNK_MAX_CHARS)
    estimated_s = _estimate_seconds(len(text))
    job_id = uuid.uuid4().hex
    job = {
        "user_id": user_id,
        "status": "queued",
        "preset_id": preset["id"],
        "preset_name": preset["name"],
        "text": text,
        "language": req.language,
        "style": style,
        "stability": stability,
        "chunks": chunks,
        "chunks_done": 0,
        "total_chunks": len(chunks),
        "audio_url": None,
        "sample_rate": None,
        "error": None,
        "submitted_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "estimated_s": estimated_s,
    }
    with _jobs_lock:
        _enqueue_job_locked(job_id, job)
        position = _queue_position_locked(job_id)

    return GenerateJobStart(
        job_id=job_id, total_chunks=len(chunks), estimated_s=estimated_s, queue_position=position,
    )


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user_id: str = Depends(get_current_user)) -> JobStatusResponse:
    with _jobs_lock:
        if job_id not in _jobs or _jobs[job_id].get("user_id") != user_id:
            raise HTTPException(404, "Unknown job_id")
        return _job_status_response_locked(job_id)


@app.get("/api/queue")
def list_queue(user_id: str = Depends(get_current_user)) -> dict:
    with _jobs_lock:
        entries = [
            _queue_entry_locked(job_id)
            for job_id, job in _jobs.items()
            if job.get("user_id") == user_id
        ]
    return {"queue": entries}


@app.post("/api/queue/{job_id}/cancel")
def cancel_queued_job(job_id: str, user_id: str = Depends(get_current_user)):
    with _jobs_lock:
        if job_id not in _jobs or _jobs[job_id].get("user_id") != user_id:
            raise HTTPException(404, "Unknown job_id")
        if job_id not in _pending_job_ids:
            raise HTTPException(
                400, "Only queued (not yet started) jobs can be canceled.",
            )
        _pending_job_ids.remove(job_id)
        _jobs[job_id].update(status="canceled", finished_at=time.time())
        _persist_queue_locked()
    credits.release_reservation(user_id)
    return {"ok": True}


@app.post("/api/queue/reorder")
def reorder_queue(req: ReorderRequest, user_id: str = Depends(get_current_user)):
    with _jobs_lock:
        owned_pending = [jid for jid in _pending_job_ids if _jobs[jid].get("user_id") == user_id]
        if set(req.job_ids) != set(owned_pending):
            raise HTTPException(
                400,
                "job_ids must be exactly the set of currently queued (not yet started) job ids.",
            )
        # Splice this user's jobs back into their own slots in the new order,
        # preserving the relative position of every other user's queued jobs
        # (the FIFO queue is shared, so reordering must not let one user's
        # request move another user's job earlier or later).
        new_order = iter(req.job_ids)
        _pending_job_ids[:] = [
            next(new_order) if _jobs[jid].get("user_id") == user_id else jid
            for jid in _pending_job_ids
        ]
        _persist_queue_locked()
    return {"ok": True}


# ---- Frontend (production) --------------------------------------------------
# Serves the built React app so a deployed pod's single exposed port is the
# only origin the browser ever talks to. Registered last -- Starlette checks
# routes in registration order and stops at the first match, so every
# /api/*, /audio/*, and /refs/* route above always wins first; this can never
# shadow them. Only activates when frontend/dist exists (i.e. `npm run build`
# has run) -- in local dev, where Vite's own dev server handles the frontend
# on :5173, frontend/dist doesn't exist and this block never registers.
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api/"):
            # A genuinely unmatched /api/* path -- report a real 404 instead
            # of silently handing back index.html and masking the bug.
            raise HTTPException(404, "Not Found")

        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)

        # Everything else -- including client-side routes like /studio,
        # /sign-in, /sign-up -- falls through to index.html; React Router
        # takes over from there.
        return FileResponse(FRONTEND_DIST / "index.html")
