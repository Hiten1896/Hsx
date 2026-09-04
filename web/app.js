const messages = document.querySelector('#messages');
const welcome = document.querySelector('#welcome-block');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message-input');
const artifactList = document.querySelector('#artifact-list');
const shell = document.querySelector('.app-shell');
const conversation = document.querySelector('.conversation');
const projects = document.querySelector('#projects-view');

const GREETINGS = {
  morning: [
    { headline: 'Good morning.', subtext: "Add a document to get started, or ask me about something you've already shared." },
    { headline: 'Morning — coffee first, or straight to it?', subtext: "Either way, drop a file in whenever you're ready and I'll dig through it." },
    { headline: 'Rise and shine.', subtext: "A report, a contract, a slide deck — bring it over and let's see what's in it." },
    { headline: 'First thing on the list?', subtext: 'Upload something new, or pick up a thread from a document already here.' },
    { headline: 'Fresh start.', subtext: 'I can pull specifics out of tables, pages, or slides — just point me at a file.' },
  ],
  afternoon: [
    { headline: 'Good afternoon.', subtext: 'Send over a document and ask me anything about it — text, tables, or diagrams.' },
    { headline: 'Midday check-in — what are we digging into?', subtext: 'Pick up an earlier document or bring a new one.' },
    { headline: 'Afternoon.', subtext: "Ask me anything about a file you've shared, or hand me a new one to work through." },
    { headline: 'Back at it?', subtext: "A document, a question — I'll go find the answer in there." },
    { headline: "Let's make some progress.", subtext: "Drop in a file and tell me what you're trying to figure out." },
  ],
  evening: [
    { headline: 'Good evening.', subtext: 'Something to review before the day wraps up? Send it over.' },
    { headline: 'Evening.', subtext: 'Upload a document or keep going on one already here — whichever works.' },
    { headline: 'Winding down, or just getting started?', subtext: "Either way, I'm ready when a file comes in." },
    { headline: "One more thing before you're done for the day?", subtext: "Hand it over and I'll get you the answer." },
    { headline: "Evening — what's on your desk?", subtext: 'Bring a document and ask away, tables and diagrams included.' },
  ],
  night: [
    { headline: 'Still up?', subtext: 'No judgment — send over whatever you’re working through.' },
    { headline: 'Burning the midnight oil.', subtext: "A document, a deadline, or both — I'm here either way." },
    { headline: 'Late one, huh?', subtext: "Let's knock it out. Drop the file in and ask your question." },
    { headline: 'Quiet hours, better focus.', subtext: "Share what you're looking at and I'll get straight to it." },
    { headline: 'Working past midnight?', subtext: "Bring the document — I don't need sleep, so take your time." },
  ],
};

function setGreeting() {
  const hour = new Date().getHours();
  const period = hour < 4 || hour >= 20 ? 'night' : hour < 12 ? 'morning' : hour < 16 ? 'afternoon' : 'evening';
  const pool = GREETINGS[period];
  const storageKey = `docagent_greeting_idx_${period}`;
  let nextIndex = 0;
  try {
    const stored = window.localStorage.getItem(storageKey);
    const parsed = stored === null ? -1 : parseInt(stored, 10);
    nextIndex = Number.isFinite(parsed) ? (parsed + 1) % pool.length : 0;
    window.localStorage.setItem(storageKey, String(nextIndex));
  } catch { nextIndex = 0; }
  const pick = pool[nextIndex];
  document.querySelector('#greeting-headline').textContent = pick.headline;
  document.querySelector('#greeting-subtext').textContent = pick.subtext;
}

function addMessage(role, text) {
  welcome.style.display = 'none';
  const item = document.createElement('div');
  item.className = `message ${role}`;
  item.innerHTML = `<div class="message-avatar">${role === 'user' ? 'A' : 'h'}</div><div class="message-body"></div>`;
  item.querySelector('.message-body').textContent = text;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

function showArtifacts(files = []) {
  if (!files.length) {
    artifactList.innerHTML = '<div class="empty-artifacts"><div class="empty-icon">◇</div><strong>Your work, in one place.</strong><p>Generated files will appear here as you make them.</p></div>';
    return;
  }
  artifactList.innerHTML = files.map(file => `<a class="artifact" href="/api/outputs/${encodeURIComponent(file.name)}"><div class="artifact-icon">${file.type.toUpperCase().slice(0, 4)}</div><div><strong>${file.name}</strong><small>Download artifact</small></div><span class="download-icon">↓</span></a>`).join('');
}

async function loadArtifacts() {
  const result = await fetch('/api/outputs');
  const data = await result.json();
  showArtifacts(data.files);
}

async function loadMemory() {
  const result = await fetch('/api/memory');
  const data = await result.json();
  document.querySelector('#memory-list').innerHTML = data.facts.length
    ? data.facts.map(fact => `<li>${fact}</li>`).join('')
    : '<li class="memory-empty">No saved context yet.</li>';
}

function switchView(view) {
  document.querySelectorAll('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === view));
  if (view === 'projects') {
    conversation.hidden = true;
    projects.hidden = false;
    shell.classList.remove('show-artifacts');
  } else if (view === 'artifacts') {
    conversation.hidden = false;
    projects.hidden = true;
    shell.classList.add('show-artifacts');
  } else {
    conversation.hidden = false;
    projects.hidden = true;
    shell.classList.remove('show-artifacts');
    input.focus();
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addMessage('user', text);
  input.value = '';
  input.style.height = 'auto';
  const pending = document.createElement('div');
  pending.className = 'message assistant';
  pending.innerHTML = '<div class="message-avatar">h</div><div class="message-body">Thinking...</div>';
  messages.appendChild(pending);
  try {
    const result = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
    const data = await result.json();
    pending.remove();
    addMessage('assistant', data.response || data.error || 'Something went wrong.');
    showArtifacts(data.outputs);
  } catch (error) {
    pending.remove();
    addMessage('assistant', 'I could not reach the local workspace server. Is it running on port 8000?');
  }
}

input.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); form.requestSubmit(); } });
form.addEventListener('submit', sendMessage);
document.querySelector('#new-chat').addEventListener('click', () => { messages.innerHTML = ''; welcome.style.display = ''; input.focus(); });
document.querySelector('#mobile-menu').addEventListener('click', () => document.querySelector('.sidebar').classList.toggle('open'));
document.querySelector('#attach-button').addEventListener('click', () => document.querySelector('#file-input').click());
document.querySelector('#file-input').addEventListener('change', event => {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async () => {
    const result = await fetch('/api/upload', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename: file.name, content: reader.result }) });
    const data = await result.json();
    addMessage('assistant', data.success ? `Uploaded ${data.name} to the read-only uploads zone.` : (data.error || 'Upload failed.'));
  };
  reader.readAsText(file);
});
document.querySelector('#skills-button').addEventListener('click', async () => {
  const result = await fetch('/api/skills');
  const data = await result.json();
  addMessage('assistant', `Available skills: ${data.skills.join(', ')}`);
});
document.querySelector('#skills-button-top').addEventListener('click', async () => {
  const result = await fetch('/api/skills');
  const data = await result.json();
  addMessage('assistant', `Available skills: ${data.skills.join(', ')}`);
});
document.querySelector('.mic-button').addEventListener('click', () => {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    addMessage('assistant', 'Voice input is not supported in this browser.');
    return;
  }
  const recognition = new SpeechRecognition();
  recognition.lang = 'en-US';
  recognition.onresult = event => { input.value = event.results[0][0].transcript; input.focus(); };
  recognition.start();
});
document.querySelector('#theme-button').addEventListener('click', () => document.body.classList.toggle('dark-mode'));
document.querySelector('#collapse-button').addEventListener('click', () => document.querySelector('.app-shell').classList.toggle('rail-collapsed'));
document.querySelector('#memory-button').addEventListener('click', async () => {
  const dialog = document.querySelector('#memory-dialog');
  await loadMemory();
  dialog.showModal();
});
document.querySelector('#save-memory').addEventListener('click', async () => {
  const memoryInput = document.querySelector('#memory-input');
  if (!memoryInput.value.trim()) return;
  await fetch('/api/memory', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ fact: memoryInput.value.trim() }) });
  memoryInput.value = '';
  await loadMemory();
});
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => switchView(button.dataset.view)));
document.querySelectorAll('[data-recent]').forEach(button => button.addEventListener('click', () => { switchView('chat'); addMessage('assistant', `Opening “${button.dataset.recent}”. Continue the conversation in the composer below.`); }));
document.querySelectorAll('[data-project]').forEach(button => button.addEventListener('click', () => { switchView('chat'); addMessage('assistant', `Project “${button.dataset.project}” is ready. What should we work on?`); }));
document.querySelector('.close-panel').addEventListener('click', () => switchView('chat'));
loadArtifacts();
setGreeting();