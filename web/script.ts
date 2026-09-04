type ChatMessage = { role: 'user' | 'assistant'; text: string };
type Chat = { id: string; title: string; messages: ChatMessage[] };
type ApiFile = { name: string; type?: string };

type ElementMap = {
  workspace: HTMLElement;
  sidebar: HTMLElement;
  conversation: HTMLElement;
  welcome: HTMLElement;
  message: HTMLInputElement;
  files: HTMLElement;
  chatList: HTMLElement;
  chatTitle: HTMLElement;
  artifactCount: HTMLElement;
  emptyArtifact: HTMLElement;
  file: HTMLInputElement;
};

const element = <K extends keyof ElementMap>(id: K): ElementMap[K] => {
  const node = document.getElementById(id === 'message' ? 'message' : id === 'chatList' ? 'chat-list' : id === 'chatTitle' ? 'chat-title' : id === 'artifactCount' ? 'artifact-count' : id === 'emptyArtifact' ? 'empty-artifact' : id === 'file' ? 'file' : id);
  if (!node) throw new Error(`Missing required element: ${id}`);
  return node as ElementMap[K];
};

const ui = {
  workspace: element('workspace'),
  sidebar: element('sidebar'),
  conversation: element('conversation'),
  welcome: element('welcome'),
  message: element('message'),
  files: element('files'),
  chatList: element('chatList'),
  chatTitle: element('chatTitle'),
  artifactCount: element('artifactCount'),
  emptyArtifact: element('emptyArtifact'),
  file: element('file'),
};

const chatStorageKey = 'hsx-chats';
let chats = readChats();
let activeChatId = chats[0]?.id ?? null;

function readChats(): Chat[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(chatStorageKey) ?? '[]');
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is Chat => {
      if (!item || typeof item !== 'object') return false;
      const chat = item as Partial<Chat>;
      return typeof chat.id === 'string' && typeof chat.title === 'string' && Array.isArray(chat.messages);
    });
  } catch {
    return [];
  }
}

function saveChats(): void { localStorage.setItem(chatStorageKey, JSON.stringify(chats)); }
function activeChat(): Chat | undefined { return chats.find(chat => chat.id === activeChatId); }
function escapeHtml(value: string): string { const node = document.createElement('div'); node.textContent = value; return node.innerHTML; }

function createChat(): void {
  const chat: Chat = { id: `${Date.now()}-${Math.random().toString(36).slice(2)}`, title: 'New Chat', messages: [] };
  chats.unshift(chat); activeChatId = chat.id; saveChats(); renderChatList(); renderActiveChat(); ui.message.focus();
}

function renderChatList(): void {
  ui.chatList.innerHTML = chats.length ? chats.map(chat => `<div class="chat-row ${chat.id === activeChatId ? 'active' : ''}" data-id="${chat.id}"><button>${escapeHtml(chat.title)}</button><button class="dots" aria-label="Actions for ${escapeHtml(chat.title)}">⋯</button></div>`).join('') : '<small>No chats yet.</small>';
  ui.chatList.querySelectorAll<HTMLElement>('.chat-row').forEach(row => {
    const id = row.dataset.id;
    if (!id) return;
    row.querySelector('button:first-child')?.addEventListener('click', () => { activeChatId = id; renderChatList(); renderActiveChat(); });
    row.querySelector('.dots')?.addEventListener('click', event => { event.stopPropagation(); showChatMenu(row, id); });
  });
}

function renderActiveChat(): void {
  const chat = activeChat();
  ui.chatTitle.textContent = chat?.title ?? 'New Chat';
  ui.conversation.innerHTML = '';
  if (!chat?.messages.length) { ui.conversation.append(ui.welcome); ui.welcome.style.display = 'block'; return; }
  ui.welcome.style.display = 'none'; chat.messages.forEach(message => renderMessage(message.role, message.text, false));
}

function showChatMenu(anchor: HTMLElement, id: string): void {
  document.querySelector('.menu')?.remove();
  const menu = document.createElement('div'); menu.className = 'menu'; menu.innerHTML = '<button data-action="rename">Rename</button><button data-action="delete">Delete</button>'; anchor.append(menu);
  menu.querySelector('[data-action="rename"]')?.addEventListener('click', () => renameChat(id));
  menu.querySelector('[data-action="delete"]')?.addEventListener('click', () => deleteChat(id));
}

function renameChat(id: string): void {
  const chat = chats.find(item => item.id === id); if (!chat) return;
  const title = window.prompt('Chat name', chat.title)?.trim(); if (!title) return;
  chat.title = title.slice(0, 80); saveChats(); renderChatList(); renderActiveChat();
}

function deleteChat(id: string): void {
  const chat = chats.find(item => item.id === id); if (!chat || !window.confirm(`Delete “${chat.title}”?`)) return;
  chats = chats.filter(item => item.id !== id); activeChatId = chats[0]?.id ?? null; saveChats();
  if (!activeChatId) createChat(); else { renderChatList(); renderActiveChat(); }
}

function renderMessage(role: ChatMessage['role'], text: string, persist = true): void {
  ui.welcome.style.display = 'none';
  const item = document.createElement('div'); item.className = `message ${role}`;
  item.innerHTML = `${role === 'assistant' ? '<div class="avatar">H</div>' : ''}<div class="bubble"></div>`;
  item.querySelector<HTMLElement>('.bubble')!.textContent = text; ui.conversation.append(item); ui.conversation.scrollTop = ui.conversation.scrollHeight;
  if (persist) { const chat = activeChat(); if (chat) { chat.messages.push({ role, text }); if (chat.title === 'New Chat' && role === 'user') chat.title = text.slice(0, 42); saveChats(); renderChatList(); ui.chatTitle.textContent = chat.title; } }
}

async function sendMessage(event: SubmitEvent): Promise<void> {
  event.preventDefault(); const text = ui.message.value.trim(); if (!text) return; if (!activeChatId) createChat();
  renderMessage('user', text); ui.message.value = '';
  const pending = document.createElement('div'); pending.className = 'message assistant'; pending.innerHTML = '<div class="avatar">H</div><div class="bubble">Thinking...</div>'; ui.conversation.append(pending);
  try { const response = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) }); const data = await response.json() as { response?: string; error?: string }; pending.remove(); renderMessage('assistant', data.response ?? data.error ?? 'Something went wrong.'); await loadFiles(); }
  catch { pending.remove(); renderMessage('assistant', 'Could not reach the local workspace server.'); }
}

async function loadFiles(): Promise<void> {
  const response = await fetch('/api/outputs'); const data = await response.json() as { files: ApiFile[] }; ui.artifactCount.textContent = data.files.length ? `${data.files.length} Active` : 'No artifacts'; ui.emptyArtifact.hidden = data.files.length > 0; ui.files.innerHTML = data.files.length ? data.files.map(file => `<a href="/api/outputs/${encodeURIComponent(file.name)}" download>${escapeHtml(file.name)}</a>`).join('') : '<small>No generated files yet.</small>';
}

async function uploadFile(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return;
  const content = await file.text(); const response = await fetch('/api/upload', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filename: file.name, content }) }); const data = await response.json() as { success?: boolean; name?: string; error?: string }; renderMessage('assistant', data.success ? `Uploaded ${data.name}.` : data.error ?? 'Upload failed.'); ui.file.value = '';
}

$('#new-chat').addEventListener('click', createChat);
$('#chat-form').addEventListener('submit', sendMessage);
$('#attach').addEventListener('click', () => ui.file.click());
ui.file.addEventListener('change', event => void uploadFile(event));
$('#sidebar-close').addEventListener('click', () => ui.workspace.classList.add('closed'));
$('#sidebar-open').addEventListener('click', () => ui.workspace.classList.remove('closed'));
$('#artifact-close').addEventListener('click', () => $('#artifacts').classList.add('closed'));
$('#artifact-open').addEventListener('click', () => $('#artifacts').classList.remove('closed'));
$('#artifact-expand').addEventListener('click', () => $('#artifacts').classList.toggle('expanded'));
$('#title-menu').addEventListener('click', () => { const chat = activeChat(); if (chat) showChatMenu($('#title-menu').parentElement!, chat.id); });
document.addEventListener('click', event => { if (!(event.target as HTMLElement).closest('.menu, .dots, #title-menu')) document.querySelector('.menu')?.remove(); });

let resizing = false; const resize = $('#resize');
resize.addEventListener('pointerdown', event => { resizing = true; resize.setPointerCapture(event.pointerId); document.body.classList.add('resizing'); });
resize.addEventListener('pointermove', event => { if (resizing) $('#artifacts').style.flexBasis = `${Math.min(720, Math.max(300, innerWidth - event.clientX))}px`; });
resize.addEventListener('pointerup', () => { resizing = false; document.body.classList.remove('resizing'); });

function $(selector: string): HTMLElement { const node = document.querySelector<HTMLElement>(selector); if (!node) throw new Error(`Missing element: ${selector}`); return node; }

if (!chats.length) createChat(); else { renderChatList(); renderActiveChat(); }
void loadFiles();
