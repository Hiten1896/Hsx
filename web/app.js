const messages = document.querySelector('#messages');
const welcome = document.querySelector('#welcome-block');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#message-input');
const artifactList = document.querySelector('#artifact-list');

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
  if (!files.length) return;
  artifactList.innerHTML = files.map(file => `<div class="artifact"><div class="artifact-icon">${file.type.toUpperCase().slice(0, 4)}</div><div><strong>${file.name}</strong><small>Ready to present</small></div></div>`).join('');
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

input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; });
input.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) form.requestSubmit(); });
form.addEventListener('submit', sendMessage);
document.querySelector('#new-chat').addEventListener('click', () => { messages.innerHTML = ''; welcome.style.display = ''; input.focus(); });
document.querySelector('#mobile-menu').addEventListener('click', () => document.querySelector('.sidebar').classList.toggle('open'));