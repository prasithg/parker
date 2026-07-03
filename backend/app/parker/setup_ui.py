"""The onboarding wizard page — served by the engine, shown by the shell.

``GET /setup/ui`` is the desktop app's first-run window, and it works
from a plain browser too (``parker serve`` + open the URL). Serving it
from the engine keeps every wizard call same-origin — config writes,
the mic level check (the TCC permission moment), voice preview, and the
model download all hit the ``/setup`` endpoints next door.

Single file, no external resources, no analytics, no fonts fetched —
the same posture as the dad screen and review pages. The wizard never
handles secrets: there is deliberately no API-key field (keys are
env-or-keychain, administered outside this surface).
"""

from __future__ import annotations

SETUP_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up Parker</title>
<style>
  :root {
    --ink: #1a2233; --muted: #5a6478; --line: #d8dde8;
    --accent: #3b5bdb; --accent-ink: #ffffff; --ok: #2b8a3e; --warn: #e8590c;
    --card: #ffffff; --bg: #f2f4f8;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 17px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    display: flex; min-height: 100vh; align-items: center; justify-content: center;
  }
  main { width: min(680px, 94vw); padding: 24px 0 40px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 16px;
    padding: 34px 38px; box-shadow: 0 8px 30px rgba(26,34,51,.07);
  }
  h1 { font-size: 28px; margin: 0 0 6px; }
  h2 { font-size: 22px; margin: 0 0 4px; }
  p { margin: 10px 0; }
  .muted { color: var(--muted); font-size: 15px; }
  .steps { display: flex; gap: 6px; margin: 0 0 22px; }
  .steps span { flex: 1; height: 5px; border-radius: 3px; background: var(--line); }
  .steps span.done { background: var(--accent); }
  label { display: block; font-weight: 600; margin: 18px 0 6px; }
  input[type=text], select {
    width: 100%; font: inherit; padding: 12px 14px; border: 1.5px solid var(--line);
    border-radius: 10px; background: #fff; color: inherit;
  }
  input[type=text]:focus, select:focus { outline: 2px solid var(--accent); border-color: transparent; }
  .row { display: flex; gap: 10px; align-items: center; }
  .nav { display: flex; justify-content: space-between; margin-top: 28px; }
  button {
    font: inherit; font-weight: 600; padding: 12px 22px; border-radius: 10px;
    border: 1.5px solid var(--line); background: #fff; color: var(--ink); cursor: pointer;
  }
  button.primary { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
  button:disabled { opacity: .45; cursor: default; }
  .consent {
    border: 1.5px solid var(--line); border-radius: 12px; padding: 16px 18px; margin: 14px 0;
  }
  .consent h3 { margin: 0 0 8px; font-size: 17px; }
  .consent ul { margin: 8px 0 0 18px; padding: 0; }
  .consent li { margin: 4px 0; }
  .optin { display: flex; gap: 10px; align-items: flex-start; margin-top: 10px; }
  .optin input { width: 20px; height: 20px; margin-top: 3px; }
  .meter { height: 18px; border-radius: 9px; background: var(--line); overflow: hidden; margin: 14px 0 6px; }
  .meter > div { height: 100%; width: 0%; background: var(--ok); transition: width .15s; }
  .status { min-height: 24px; font-size: 15px; }
  .status.ok { color: var(--ok); } .status.warn { color: var(--warn); }
  .big { font-size: 40px; text-align: center; margin: 8px 0; }
  .hidden { display: none; }
  progress { width: 100%; height: 14px; }
</style>
</head>
<body>
<main>
  <div class="card">
    <div class="steps" id="stepdots"></div>
    <div id="step-welcome">
      <div class="big">👋</div>
      <h1>Let's set up Parker</h1>
      <p>Parker is a voice assistant built for people whose speech takes
      effort. It listens patiently, checks before it acts, and keeps the
      family in the loop.</p>
      <p class="muted">A few questions for the family administrator —
      about two minutes, plus a one-time speech-model download.</p>
      <p class="muted">Parker never gives medical advice, never changes
      medication, and is not an emergency service.</p>
    </div>

    <div id="step-name" class="hidden">
      <h2>Who is Parker for?</h2>
      <p class="muted">The first name Parker will use when speaking.</p>
      <label for="patient_name">First name</label>
      <input type="text" id="patient_name" placeholder="Dad" autocomplete="off">
    </div>

    <div id="step-contacts" class="hidden">
      <h2>Family contacts</h2>
      <p class="muted">People Parker may prepare messages for. A message to
      someone on this list is released on the patient's own spoken yes;
      anyone else stays waiting for a family review. Leave empty to keep
      every message waiting for review.</p>
      <label for="contacts">Names, separated by commas</label>
      <input type="text" id="contacts" placeholder="Sarah, Michael" autocomplete="off">
    </div>

    <div id="step-lexicon" class="hidden">
      <h2>Words Parker should know</h2>
      <p class="muted">Places, routines, nicknames — everyday words that help
      Parker hear this person correctly. Family contact names are included
      automatically.</p>
      <label for="lexicon">Extra words, separated by commas</label>
      <input type="text" id="lexicon" placeholder="physio, bridge night, Priya" autocomplete="off">
    </div>

    <div id="step-voice" class="hidden">
      <h2>Parker's voice</h2>
      <p class="muted">Pick the voice for spoken replies and hear a preview.</p>
      <label for="voice">Voice</label>
      <div class="row">
        <select id="voice"><option value="">System default</option></select>
        <button id="preview" type="button">▶ Preview</button>
      </div>
      <div id="voice-status" class="status"></div>
    </div>

    <div id="step-consent" class="hidden">
      <h2>What Parker keeps — in plain language</h2>
      <div class="consent">
        <h3>Stored on this computer only</h3>
        <ul>
          <li>These settings (a small file — never passwords or keys).</li>
          <li>Reminders and drafted messages awaiting a spoken yes.</li>
          <li>What Parker heard, as text, for the family review page.</li>
        </ul>
      </div>
      <div class="consent">
        <h3>What never happens</h3>
        <ul>
          <li>Nothing is sent anywhere — v0 has no send path at all.</li>
          <li>Audio recordings are never kept; they are deleted the moment
          they are turned into text.</li>
          <li>No account, no cloud, no analytics.</li>
        </ul>
      </div>
      <div class="consent">
        <h3>Optional: help Parker learn (off unless you turn it on)</h3>
        <p class="muted">Keep a local, text-only record of exchanges where
        Parker misheard and was corrected, so understanding can improve.
        Stays on this computer. Never audio.</p>
        <div class="optin">
          <input type="checkbox" id="repair_consent">
          <label for="repair_consent" style="margin:0;font-weight:500">
            Yes, keep local repair notes</label>
        </div>
      </div>
    </div>

    <div id="step-mic" class="hidden">
      <h2>Microphone check</h2>
      <p class="muted">Click the button and say a few words. macOS will ask
      for microphone permission — that's expected; choose <b>Allow</b>.</p>
      <div class="meter"><div id="miclevel"></div></div>
      <div id="mic-status" class="status"></div>
      <p><button id="micbtn" class="primary" type="button">Check the microphone</button></p>
    </div>

    <div id="step-model" class="hidden">
      <h2>Speech model download</h2>
      <p class="muted">Parker understands speech entirely on this computer.
      That needs a one-time download of the speech model (about 150&nbsp;MB).
      Nothing spoken ever goes online.</p>
      <progress id="modelbar" max="100" value="0"></progress>
      <div id="model-status" class="status"></div>
      <p><button id="modelbtn" class="primary" type="button">Download the model</button></p>
    </div>

    <div id="step-done" class="hidden">
      <div class="big">✅</div>
      <h1>Parker is ready</h1>
      <p>Parker lives in the menu bar: open the <b>Dad Screen</b> on the TV
      or a spare monitor, start listening, and speak naturally.</p>
      <p class="muted">The family review page shows everything waiting for a
      look, and the daily digest sums up what happened — all local.</p>
      <p class="muted">Recording a few real phrases helps tune understanding:
      see <code>docs/pilot-recording-protocol.md</code> in the Parker
      project for the two-minute protocol.</p>
    </div>

    <div class="nav">
      <button id="back" type="button">Back</button>
      <button id="next" class="primary" type="button">Get started</button>
    </div>
  </div>
</main>
<script>
(function () {
  "use strict";
  var steps = ["welcome","name","contacts","lexicon","voice","consent","mic","model","done"];
  var i = 0;
  var micChecked = false, modelReady = false, saved = false;

  var dots = document.getElementById("stepdots");
  steps.forEach(function(){ dots.appendChild(document.createElement("span")); });

  function show() {
    steps.forEach(function (name, idx) {
      document.getElementById("step-" + name).classList.toggle("hidden", idx !== i);
      dots.children[idx].classList.toggle("done", idx <= i);
    });
    var next = document.getElementById("next");
    var back = document.getElementById("back");
    back.style.visibility = (i === 0 || i === steps.length - 1) ? "hidden" : "visible";
    next.textContent = i === 0 ? "Get started"
      : (steps[i] === "model" ? "Finish" : (steps[i] === "done" ? "Close this window" : "Continue"));
    next.disabled = (steps[i] === "mic" && !micChecked) || (steps[i] === "model" && !modelReady);
  }

  function post(url, body) {
    return fetch(url, { method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body || {}) }).then(function (r) {
        return r.json().then(function (j) { if (!r.ok) throw j; return j; });
      });
  }

  // Voice picker
  fetch("/setup/tts-voices").then(function(r){ return r.json(); }).then(function (data) {
    var select = document.getElementById("voice");
    (data.voices || []).sort(function (a, b) {
      var ae = a.lang.indexOf("en") === 0 ? 0 : 1, be = b.lang.indexOf("en") === 0 ? 0 : 1;
      return ae - be || a.name.localeCompare(b.name);
    }).forEach(function (v) {
      var option = document.createElement("option");
      option.value = v.name; option.textContent = v.name + " (" + v.lang + ")";
      select.appendChild(option);
    });
    if (data.current) select.value = data.current;
  }).catch(function(){});

  document.getElementById("preview").addEventListener("click", function () {
    var status = document.getElementById("voice-status");
    status.textContent = "Speaking…"; status.className = "status";
    post("/setup/tts-preview", { voice: document.getElementById("voice").value })
      .then(function (r) {
        status.textContent = r.spoke ? "That's the voice Parker will use." :
          "Couldn't speak on this machine.";
        status.className = r.spoke ? "status ok" : "status warn";
      }).catch(function () { status.textContent = "Preview failed."; status.className = "status warn"; });
  });

  // Mic check — the TCC permission moment.
  document.getElementById("micbtn").addEventListener("click", function () {
    var status = document.getElementById("mic-status");
    var button = this;
    button.disabled = true;
    status.textContent = "Listening for a moment — say something…"; status.className = "status";
    post("/setup/mic-check", { seconds: 1.5 }).then(function (r) {
      var pct = Math.min(100, Math.round((r.rms / 3000) * 100));
      document.getElementById("miclevel").style.width = Math.max(pct, r.heard_anything ? 8 : 0) + "%";
      if (r.heard_anything) {
        micChecked = true;
        status.textContent = "Heard you on “" + r.device + "”. You can re-check or continue.";
        status.className = "status ok";
      } else {
        status.textContent = "The microphone opened but everything was silent — check the input device or macOS microphone permission (System Settings → Privacy & Security → Microphone), then try again.";
        status.className = "status warn";
      }
    }).catch(function (err) {
      status.textContent = "Couldn't open the microphone: " + (err.detail || "unknown error") +
        " — if macOS asked for permission and it was declined, allow Parker in System Settings → Privacy & Security → Microphone and try again.";
      status.className = "status warn";
    }).finally(function () { button.disabled = false; show(); });
  });

  // Model download with progress.
  var pollTimer = null;
  function pollModel() {
    fetch("/setup/model/status").then(function(r){ return r.json(); }).then(function (s) {
      var status = document.getElementById("model-status");
      var bar = document.getElementById("modelbar");
      var mb = Math.round((s.bytes_downloaded || 0) / 1048576);
      if (s.state === "ready") {
        clearInterval(pollTimer); pollTimer = null;
        bar.value = 100; modelReady = true;
        status.textContent = s.location === "hf_cache" ?
          "A speech model is already on this computer — nothing to download." :
          "Model ready (" + (mb || 150) + " MB, stored locally).";
        status.className = "status ok";
        document.getElementById("modelbtn").classList.add("hidden");
      } else if (s.state === "downloading") {
        bar.removeAttribute("value");
        status.textContent = "Downloading… " + mb + " MB so far.";
        status.className = "status";
      } else if (s.state === "error") {
        clearInterval(pollTimer); pollTimer = null; bar.value = 0;
        status.textContent = "Download failed: " + s.error + " — check the internet connection and try again.";
        status.className = "status warn";
        document.getElementById("modelbtn").disabled = false;
      }
      show();
    }).catch(function(){});
  }
  document.getElementById("modelbtn").addEventListener("click", function () {
    this.disabled = true;
    post("/setup/model/download", {}).then(function () {
      if (!pollTimer) pollTimer = setInterval(pollModel, 1000);
    }).catch(function () { this.disabled = false; }.bind(this));
  });

  function saveConfig() {
    return post("/setup/config", { settings: {
      patient_name: document.getElementById("patient_name").value.trim() || "Dad",
      parker_family_contacts: document.getElementById("contacts").value.trim(),
      personal_lexicon: document.getElementById("lexicon").value.trim(),
      parker_tts_voice: document.getElementById("voice").value,
      repair_event_capture_consented: document.getElementById("repair_consent").checked,
      onboarding_completed: true
    }});
  }

  document.getElementById("next").addEventListener("click", function () {
    if (steps[i] === "model" && !saved) {
      saveConfig().then(function () { saved = true; i += 1; show(); })
        .catch(function (err) { alert("Could not save settings: " + (err.detail || "unknown")); });
      return;
    }
    if (steps[i] === "done") { window.close(); return; }
    i = Math.min(i + 1, steps.length - 1); show();
    if (steps[i] === "model") pollModel();  // pre-check: maybe already cached
  });
  document.getElementById("back").addEventListener("click", function () {
    i = Math.max(i - 1, 0); show();
  });
  show();
})();
</script>
</body>
</html>
"""
