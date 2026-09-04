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
    { headline: 'Still up?', subtext: "No judgment — send over whatever you're working through." },
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
  const storageKey = `hsx_greeting_idx_${period}`;
  let nextIndex = 0;
  try {
    const stored = window.localStorage.getItem(storageKey);
    const parsed = stored === null ? -1 : parseInt(stored, 10);
    nextIndex = Number.isFinite(parsed) ? (parsed + 1) % pool.length : 0;
    window.localStorage.setItem(storageKey, String(nextIndex));
  } catch {
    nextIndex = 0;
  }
  const pick = pool[nextIndex] || pool[0];
  const headlineEl = document.querySelector('#greeting-headline');
  const subtextEl = document.querySelector('#greeting-subtext');
  if (headlineEl) headlineEl.textContent = pick.headline;
  if (subtextEl) subtextEl.textContent = pick.subtext;
}

const messages = document.querySelector('#messages');
const welcomeBlock = document.querySelector('#welcome-block');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message-input');
const attachBtn = document.querySelector('#attach-button');
const fileInput = document.querySelector('#file-input');
const micBtn = document.querySelector('#mic-button');
const themeBtn = document.querySelector('#theme-button');
const collapseBtn = document.querySelector('#collapse-button');
const newChatBtn = document.querySelector('#new-chat');
const mobileMenuBtn = document.querySelector('#mobile-menu');
const pipelineStatus = document.querySelector('#pipeline-status');

function addMessage(role, text, outputs = []) {
  if (welcomeBlock) welcomeBlock.style.display = 'none';
  const item = document.createElement('div');
  item.className = `message ${role}`;

  const avatar = role === 'user' ? 'A' : 'H';
  let artifactsHtml = '';
  if (outputs && outputs.length > 0) {
    artifactsHtml = `
      <div style="margin-top: 8px; padding-top: 8px; border-top: 1px solid #e0e5ed; font-size: 11px;">
        <strong style="display: block; margin-bottom: 4px; color: #5542d6;">Generated files:</strong>
        ${outputs.map(f => `<a href="/api/outputs/${encodeURIComponent(f.name)}" download style="display: inline-block; margin-right: 6px; padding: 4px 8px; background: #dfe6ff; color: #4034c8; border-radius: 6px; text-decoration: none; font-weight: 500;">📎 ${f.name}</a>`).join('')}
      </div>
    `;
  }

  item.innerHTML = `
    <div class="message-avatar">${avatar}</div>
    <div class="message-body">${escapeHtml(text)}${artifactsHtml}</div>
  `;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

async function sendMessage(e) {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addMessage('user', text);
  input.value = '';

  const pending = document.createElement('div');
  pending.className = 'message assistant';
  pending.innerHTML = '<div class="message-avatar">H</div><div class="message-body" style="font-style: italic; color: #7285a2;">Thinking...</div>';
  messages.appendChild(pending);
  messages.scrollTop = messages.scrollHeight;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    pending.remove();
    addMessage('assistant', data.response || data.error || 'Something went wrong.', data.outputs || []);
  } catch (err) {
    pending.remove();
    addMessage('assistant', 'Could not reach the local workspace server. Is it running on port 8000?');
  }
}

async function checkPipelineStatus() {
  try {
    const res = await fetch('/api/skills');
    if (res.ok) {
      if (pipelineStatus) {
        pipelineStatus.className = 'online';
        pipelineStatus.innerHTML = '<span></span> Online';
      }
    }
  } catch {
    if (pipelineStatus) {
      pipelineStatus.className = 'offline';
      pipelineStatus.innerHTML = '<span></span> Offline';
    }
  }
}

if (form) form.addEventListener('submit', sendMessage);

if (newChatBtn) {
  newChatBtn.addEventListener('click', () => {
    messages.innerHTML = '';
    if (welcomeBlock) welcomeBlock.style.display = '';
    setGreeting();
    if (input) input.focus();
  });
}

if (themeBtn) {
  themeBtn.addEventListener('click', () => {
    document.body.classList.toggle('dark-mode');
  });
}

const sidebarToggleBtn = document.querySelector('#sidebar-toggle-btn');
const appShell = document.querySelector('#app-shell');

if (collapseBtn) {
  collapseBtn.addEventListener('click', () => {
    if (appShell) appShell.classList.add('sidebar-closed');
  });
}

if (sidebarToggleBtn) {
  sidebarToggleBtn.addEventListener('click', () => {
    if (appShell) appShell.classList.remove('sidebar-closed');
  });
}

if (mobileMenuBtn) {
  mobileMenuBtn.addEventListener('click', () => {
    document.querySelector('.sidebar').classList.toggle('open');
  });
}

if (attachBtn && fileInput) {
  attachBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      try {
        const res = await fetch('/api/upload', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: file.name, content: reader.result })
        });
        const data = await res.json();
        addMessage('assistant', data.success ? `Uploaded ${data.name} to the read-only uploads zone.` : (data.error || 'Upload failed.'));
      } catch {
        addMessage('assistant', 'Upload failed. Could not reach server.');
      }
    };
    reader.readAsText(file);
  });
}

if (micBtn && input) {
  micBtn.addEventListener('click', () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      alert('Voice input is not supported in this browser.');
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.onresult = (e) => {
      input.value = e.results[0][0].transcript;
      input.focus();
    };
    recognition.start();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setGreeting();
  checkPipelineStatus();
});
setGreeting();
checkPipelineStatus();
