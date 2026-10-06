"use strict";

(() => {
  const root = document.querySelector("[data-interview-id]");
  const interviewId = root.dataset.interviewId;
  const api = `/api/v1/interviews/${encodeURIComponent(interviewId)}`;

  const statusText = document.getElementById("status-text");
  const statusDot = document.getElementById("status-dot");
  const timerEl = document.getElementById("timer");
  const errorEl = document.getElementById("call-error");
  const startBtn = document.getElementById("start-call");
  const endBtn = document.getElementById("end-call");
  const aiOrb = document.getElementById("ai-orb");
  const userOrb = document.getElementById("user-orb");

  const SEND_SAMPLE_RATE = 16000; // must match worker/voice_session.py + Deepgram config

  let ws = null;
  let micStream = null;
  let audioCtx = null;         // capture-side context, fixed at SEND_SAMPLE_RATE
  let playbackCtx = null;      // playback-side context, browser default rate
  let micNode = null;
  let rafId = null;
  let timerId = null;
  let maxCallSeconds = 900;
  let ending = false;
  let aiLevel = 0;
  let userLevel = 0;
  let playbackQueueEndsAt = 0;

  function setStatus(text, state) {
    statusText.textContent = text;
    statusDot.className = `dot ${state}`;
  }

  function showError(message) {
    errorEl.textContent = message;
    setStatus("Something went wrong", "error");
  }

  async function getJson(url, options = {}) {
    const resp = await fetch(url, { credentials: "same-origin", ...options });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `Request failed (${resp.status})`);
    return data;
  }

  // Poll until the backend has loaded the GitHub profile.
  async function waitUntilReady() {
    for (let i = 0; i < 60; i++) {
      const data = await getJson(api);
      maxCallSeconds = data.max_call_seconds || maxCallSeconds;
      if (data.status === "failed") throw new Error(data.error || "This interview could not be prepared.");
      if (data.status !== "pre") {
        window.location.replace(`/result/${encodeURIComponent(interviewId)}`);
        return false;
      }
      if (data.ready) return true;
      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error("Loading your GitHub profile is taking too long. Please try again.");
  }

  function startMeters() {
    const tick = () => {
      aiOrb.style.setProperty("--level", aiLevel.toFixed(3));
      userOrb.style.setProperty("--level", userLevel.toFixed(3));
      // AI level decays on its own between audio chunks so the orb doesn't stay lit forever.
      aiLevel = Math.max(0, aiLevel - 0.02);
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
  }

  function startTimer() {
    const endsAt = Date.now() + maxCallSeconds * 1000;
    const render = () => {
      const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
      timerEl.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")} left`;
      if (left === 0) endInterview();
    };
    render();
    timerId = setInterval(render, 1000);
  }

  // Downsample a Float32 buffer from the context's native rate to SEND_SAMPLE_RATE and
  // convert to 16-bit signed PCM, little-endian -- the format worker/voice_session.py and
  // Deepgram's streaming API expect.
  function floatTo16BitPCM(float32, inRate, outRate) {
    const ratio = inRate / outRate;
    const outLength = Math.floor(float32.length / ratio);
    const out = new Int16Array(outLength);
    for (let i = 0; i < outLength; i++) {
      const s = Math.max(-1, Math.min(1, float32[Math.floor(i * ratio)]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }

  function rms(float32) {
    let sum = 0;
    for (let i = 0; i < float32.length; i++) sum += float32[i] * float32[i];
    return Math.min(1, Math.sqrt(sum / float32.length) * 3.2);
  }

  async function startMicCapture() {
    audioCtx = new AudioContext();
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });
    const source = audioCtx.createMediaStreamSource(micStream);
    // ScriptProcessorNode is deprecated but universally supported without extra worklet
    // files; buffer size 4096 keeps latency low (~85-250ms depending on context rate).
    micNode = audioCtx.createScriptProcessor(4096, 1, 1);
    micNode.onaudioprocess = (e) => {
      const input = e.inputBuffer.getChannelData(0);
      userLevel = rms(input);
      if (ws && ws.readyState === WebSocket.OPEN) {
        const pcm = floatTo16BitPCM(input, audioCtx.sampleRate, SEND_SAMPLE_RATE);
        ws.send(pcm.buffer);
      }
    };
    source.connect(micNode);
    // Route through a muted gain node (not directly to destination) so the mic is never
    // audibly looped back, while still keeping the processor node alive per Web Audio rules.
    const silentGain = audioCtx.createGain();
    silentGain.gain.value = 0;
    micNode.connect(silentGain);
    silentGain.connect(audioCtx.destination);
  }

  // Plays a WAV ArrayBuffer received from the server (Sarvam TTS output) back-to-back with
  // any audio already queued, so overlapping replies don't talk over each other.
  async function playAudioChunk(arrayBuffer) {
    playbackCtx = playbackCtx || new AudioContext();
    try {
      const audioBuffer = await playbackCtx.decodeAudioData(arrayBuffer.slice(0));
      const src = playbackCtx.createBufferSource();
      src.buffer = audioBuffer;
      const analyser = playbackCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      analyser.connect(playbackCtx.destination);
      const startAt = Math.max(playbackCtx.currentTime, playbackQueueEndsAt);
      src.start(startAt);
      playbackQueueEndsAt = startAt + audioBuffer.duration;
      aiLevel = 0.6; // simple pulse while a reply is queued/playing
      src.onended = () => {
        if (playbackCtx.currentTime >= playbackQueueEndsAt - 0.05) aiLevel = 0;
      };
    } catch (err) {
      console.error("could not decode audio from server", err); // eslint-disable-line no-console
    }
  }

  function connectVoiceSocket(wsUrl) {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(wsUrl);
      socket.binaryType = "arraybuffer";
      socket.onopen = () => resolve(socket);
      socket.onerror = () => reject(new Error("Could not connect to the voice server."));
      socket.onmessage = (event) => {
        if (typeof event.data === "string") {
          try {
            const msg = JSON.parse(event.data);
            if (msg.type === "transcript") {
              // Transcript text is persisted server-side; nothing to render here today.
            }
          } catch (_) {
            /* ignore malformed control frames */
          }
          return;
        }
        playAudioChunk(event.data);
      };
      socket.onclose = () => {
        if (!ending) {
          showError("The call was disconnected.");
          endInterview();
        }
      };
    });
  }

  async function startCall() {
    startBtn.disabled = true;
    errorEl.textContent = "";
    setStatus("Connecting…", "waiting");
    try {
      const { ws_path, ws_url } = await getJson(`${api}/session`, { method: "POST" });
      const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const wsUrl = ws_url || `${wsProtocol}//${window.location.host}${ws_path}`;
      await startMicCapture();
      ws = await connectVoiceSocket(wsUrl);

      setStatus("Interview live", "live");
      startBtn.hidden = true;
      endBtn.hidden = false;
      endBtn.focus();
      startMeters();
      startTimer();
    } catch (err) {
      cleanup();
      showError(err.name === "NotAllowedError" ? "Microphone access was denied." : err.message);
      startBtn.disabled = false;
    }
  }

  function cleanup() {
    if (rafId) cancelAnimationFrame(rafId);
    if (timerId) clearInterval(timerId);
    micNode?.disconnect();
    micStream?.getTracks().forEach((t) => t.stop());
    audioCtx?.close().catch(() => {});
    playbackCtx?.close().catch(() => {});
    if (ws && ws.readyState === WebSocket.OPEN) ws.close();
    ws = null;
    micNode = null;
    micStream = null;
    audioCtx = null;
    playbackCtx = null;
  }

  async function endInterview() {
    if (ending) return;
    ending = true;
    endBtn.disabled = true;
    setStatus("Wrapping up…", "waiting");
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        ws.send(JSON.stringify({ type: "end" }));
      } catch (_) {
        /* socket may already be closing */
      }
    }
    cleanup();
    try {
      await getJson(`${api}/end`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (_) {
      /* the voice worker also finalizes when the call drops */
    }
    window.location.assign(`/result/${encodeURIComponent(interviewId)}`);
  }

  startBtn.addEventListener("click", startCall);
  endBtn.addEventListener("click", endInterview);
  window.addEventListener("pagehide", () => {
    if (ws && !ending) {
      navigator.sendBeacon?.(`${api}/end`, new Blob(["{}"], { type: "application/json" }));
    }
  });

  (async () => {
    try {
      if (!(await waitUntilReady())) return;
      setStatus("Ready when you are", "waiting");
      startBtn.disabled = false;
      startBtn.focus();
    } catch (err) {
      showError(err.message);
    }
  })();
})();
