"""Single-file live patient screen (the "dad screen").

Served at GET /parker/screen for the TV or monitor next to the person.
Big type, high contrast, and numbered cards that match the spoken
"1) ... 2) ..." choices exactly — the screen removes the working-memory
load of holding spoken options in mind. Voice remains the only input:
this page is deliberately output-only, with no buttons, no links, and no
form controls (pinned by tests). It polls the local /parker/screen/state
endpoint, which mirrors only the current exchange — never a transcript.
"""

SCREEN_PAGE_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parker — live screen</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  html, body { height: 100%; }
  body {
    margin: 0;
    font-family: -apple-system, system-ui, "Segoe UI", sans-serif;
    background: #05080d;
    color: #f4f7fb;
    display: flex;
    flex-direction: column;
    padding: 4vh 6vw 2vh;
  }
  main { flex: 1; display: flex; flex-direction: column; justify-content: center; gap: 2.2vh; }
  [hidden] { display: none !important; }

  .label {
    font-size: clamp(1rem, 1.8vw, 1.5rem);
    letter-spacing: .18em;
    text-transform: uppercase;
    color: #7d8ca1;
  }
  #heard {
    font-size: clamp(1.6rem, 3.4vw, 2.8rem);
    color: #b9c6d8;
    font-style: italic;
    line-height: 1.3;
  }
  #speech {
    font-size: clamp(2.2rem, 5vw, 4rem);
    font-weight: 650;
    line-height: 1.22;
  }
  .chip {
    align-self: flex-start;
    font-size: clamp(1.3rem, 2.6vw, 2.1rem);
    font-weight: 700;
    padding: .45em 1em;
    border-radius: 999px;
    margin-top: 1vh;
  }
  .chip.ask   { background: #ffd166; color: #05080d; }
  .chip.ok    { background: #133c1f; color: #7fe3a1; border: 3px solid #2e6b2e; }
  .chip.warn  { background: #431a1f; color: #ff9aa4; border: 3px solid #a33; }
  .chip.quiet { background: #1a2432; color: #b9c6d8; }

  #choices { margin-top: 1vh; }
  .choice {
    display: flex;
    align-items: center;
    gap: clamp(1rem, 2.5vw, 2rem);
    border: 3px solid #34435c;
    border-radius: 20px;
    background: #0c1420;
    padding: clamp(.8rem, 2vh, 1.6rem) clamp(1rem, 2.5vw, 2rem);
    margin: 1.4vh 0;
  }
  .choice .num {
    font-size: clamp(2.2rem, 4.5vw, 3.6rem);
    font-weight: 800;
    background: #ffd166;
    color: #05080d;
    border-radius: 16px;
    min-width: 2em;
    text-align: center;
    padding: .05em .2em;
  }
  .choice .text { font-size: clamp(1.8rem, 3.6vw, 3rem); line-height: 1.25; }
  #say-hint { font-size: clamp(1.2rem, 2.2vw, 1.8rem); color: #7d8ca1; }

  #idle { text-align: center; }
  #idle .dot {
    font-size: clamp(2.5rem, 5vw, 4rem);
    color: #2e6b2e;
    animation: breathe 2.4s ease-in-out infinite;
  }
  #idle h1 { font-size: clamp(2.4rem, 5vw, 4rem); margin: 1vh 0; }
  #idle p { font-size: clamp(1.4rem, 2.6vw, 2.2rem); color: #8fa0b5; }
  @keyframes breathe { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }

  footer {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    color: #55647a;
    font-size: clamp(.85rem, 1.4vw, 1.1rem);
    padding-top: 1.5vh;
  }
</style>
</head>
<body>
<main id="live" hidden>
  <div id="heard-block">
    <div class="label">You said</div>
    <div id="heard"></div>
  </div>
  <div>
    <div class="label">Parker</div>
    <div id="speech"></div>
  </div>
  <div id="chip" class="chip" hidden></div>
  <div id="choices" hidden>
    <div id="cards"></div>
    <div id="say-hint">Just say the number out loud.</div>
  </div>
</main>
<main id="idle">
  <div class="dot">●</div>
  <h1>Parker is listening</h1>
  <p>Just start talking — this screen shows what Parker hears and says.</p>
</main>
<footer>
  <span>Voice is the only input — nothing here needs to be touched.</span>
  <span id="updated"></span>
</footer>

<script>
// Status chips by response kind. awaiting wins when set, so pending cards
// keep their "say the number" cue even across a silent window.
const KIND_CHIPS = {
  captured:             ['Saved — Parker checks with you before anything runs', 'ok'],
  executed:             ['Done', 'ok'],
  choices:              ['Say the number', 'ask'],
  confirm_offer:        ['Say yes — or no', 'ask'],
  clarify:              ['One more detail needed', 'ask'],
  retry:                ['Say it again in your own words', 'ask'],
  cancelled:            ['Cancelled', 'quiet'],
  cancelled_outbox:     ['Message cancelled', 'quiet'],
  refused:              ["Parker won't do that", 'warn'],
  emergency_redirect:   ["If it's urgent, call for help — Parker can't", 'warn'],
  needs_human_approval: ['Waiting for family approval', 'warn'],
  execution_failed:     ["That didn't work — the family will see it", 'warn'],
  blocked:              ["Couldn't run", 'warn'],
  context_required:     ['Needs a room or TV set up first', 'warn'],
};
const AWAITING_CHIPS = {
  choices: ['Say the number', 'ask'],
  yes_no:  ['Say yes — or no', 'ask'],
};

function setChip(state) {
  const chip = document.getElementById('chip');
  const entry = AWAITING_CHIPS[state.awaiting] || KIND_CHIPS[state.kind];
  if (!entry) { chip.hidden = true; return; }
  chip.textContent = entry[0];
  chip.className = 'chip ' + entry[1];
  chip.hidden = false;
}

function setChoices(state) {
  const wrap = document.getElementById('choices');
  const cards = document.getElementById('cards');
  cards.textContent = '';
  const choices = state.choices || [];
  if (!choices.length) { wrap.hidden = true; return; }
  for (const choice of choices) {
    const card = document.createElement('div');
    card.className = 'choice';
    const num = document.createElement('div');
    num.className = 'num';
    num.textContent = choice.position;
    const text = document.createElement('div');
    text.className = 'text';
    text.textContent = choice.label;
    card.appendChild(num);
    card.appendChild(text);
    cards.appendChild(card);
  }
  wrap.hidden = false;
}

function agoText(iso) {
  if (!iso) return '';
  const then = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
  const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
  if (seconds < 12) return 'just now';
  if (seconds < 90) return Math.round(seconds) + 's ago';
  if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
  return then.toLocaleTimeString();
}

let lastState = null;

function render(state) {
  lastState = state;
  const live = document.getElementById('live');
  const idle = document.getElementById('idle');
  if (state.empty) { live.hidden = true; idle.hidden = false; return; }
  idle.hidden = true;
  live.hidden = false;
  const heardBlock = document.getElementById('heard-block');
  heardBlock.hidden = !state.heard;
  document.getElementById('heard').textContent = state.heard ? '“' + state.heard + '”' : '';
  document.getElementById('speech').textContent = state.speech;
  setChip(state);
  setChoices(state);
}

async function tick() {
  try {
    const res = await fetch('/parker/screen/state', {cache: 'no-store'});
    if (res.ok) render(await res.json());
  } catch (err) {
    // Server briefly away (restart, sleep): keep the last frame quietly.
  }
  if (lastState && !lastState.empty) {
    document.getElementById('updated').textContent = agoText(lastState.updated_at);
  } else {
    document.getElementById('updated').textContent = '';
  }
}

tick();
setInterval(tick, 1500);
</script>
</body>
</html>
"""
