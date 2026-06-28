(function () {
  // Prevent double injection
  if (document.getElementById('gh-agent-root')) return;

  /* ─── Toggle Button ─────────────────────────────────────────────── */
  const toggleBtn = document.createElement('button');
  toggleBtn.id = 'gh-agent-toggle';
  toggleBtn.title = 'Open GitHub Agent';
  toggleBtn.innerHTML = `
    <svg id="gh-agent-icon-open" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="12" cy="12" r="10"/>
      <path d="M12 8v4l3 3"/>
    </svg>
    <svg id="gh-agent-icon-close" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none">
      <path d="M18 6L6 18M6 6l12 12"/>
    </svg>
  `;
  document.body.appendChild(toggleBtn);

  /* ─── Side Panel ─────────────────────────────────────────────────── */
  const panel = document.createElement('div');
  panel.id = 'gh-agent-root';
  panel.setAttribute('aria-label', 'GitHub Agent Panel');
  panel.innerHTML = `
    <div id="gh-agent-header">
      <span id="gh-agent-title">GitHub Agent</span>
      <span id="gh-agent-badge">BETA</span>
    </div>

    <div id="gh-agent-context-bar">
      <span id="gh-agent-repo-label">No repo detected</span>
    </div>

    <div id="gh-agent-body">
      <div id="gh-agent-messages">
        <div class="gh-agent-empty-state">
          <p>Ask the agent anything about this repo</p>
        </div>
      </div>
    </div>

    <div id="gh-agent-footer">
      <textarea id="gh-agent-input" rows="1"></textarea>
      <button id="gh-agent-submit" disabled>Send</button>
    </div>
  `;
  document.body.appendChild(panel);

  /* ─── FIX #1: conversation history ─────────────────────────────── */
  const conversationHistory = [];

  /* ─── FIX #2: appendMessage function ───────────────────────────── */
  function appendMessage(role, text) {
    const container = document.getElementById('gh-agent-messages');

    const empty = container.querySelector('.gh-agent-empty-state');
    if (empty) empty.remove();

    const msg = document.createElement('div');
    msg.className = `gh-agent-message ${role}`;
    msg.textContent = text;

    container.appendChild(msg);
    container.scrollTop = container.scrollHeight;

    return msg;
  }

  /* ─── Detect repo ─────────────────────────────────────────────── */
  function detectRepo() {
    const match = window.location.pathname.match(/^\/([^/]+\/[^/]+)/);
    const label = document.getElementById('gh-agent-repo-label');

    if (match) {
      label.textContent = match[1];
    } else {
      label.textContent = 'No repo detected';
    }
  }
  detectRepo();

  /* ─── Toggle panel ────────────────────────────────────────────── */
  let isOpen = false;

  function openPanel() {
    isOpen = true;
    panel.classList.add('gh-agent-open');
  }

  function closePanel() {
    isOpen = false;
    panel.classList.remove('gh-agent-open');
  }

  toggleBtn.addEventListener('click', () =>
    isOpen ? closePanel() : openPanel()
  );

  /* ─── Input logic ─────────────────────────────────────────────── */
  const textarea = document.getElementById('gh-agent-input');
  const submitBtn = document.getElementById('gh-agent-submit');

  textarea.addEventListener('input', () => {
    submitBtn.disabled = textarea.value.trim().length === 0;
  });

  textarea.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      if (!submitBtn.disabled) submitBtn.click();
    }
  });

  /* ─── Submit ──────────────────────────────────────────────────── */
  submitBtn.addEventListener('click', async () => {
    const query = textarea.value.trim();
    if (!query) return;

    const match = window.location.pathname.match(/^\/([^/]+\/[^/]+)/);
    if (!match) return;

    textarea.value = '';
    submitBtn.disabled = true;

    appendMessage('user', query);
    const loadingEl = appendMessage('assistant', '...');

    try {
      const res = await fetch('http://localhost:8000/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query: query,
          repo: match[1],
          history: conversationHistory 
       })
      });

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      // Replace the current stream reading block with this:
      let finalAnswer = "";
      let isAnswer = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        const chunk = decoder.decode(value);
        const lines = chunk.split("\n").filter(line => line.startsWith("data:"));

        for (const line of lines) {
          const message = line.replace("data:", "").trim();
          if (!message) continue;

          if (message.startsWith("✅ Done")) {
            isAnswer = true;
            continue;
          }

          if (isAnswer) {
            finalAnswer += message.replace(/<br>/g, "\n") + "\n";
            loadingEl.textContent = finalAnswer;
          } else {
            // Status messages — show them as loading indicator
            loadingEl.textContent = message;
          }

          const container = document.getElementById('gh-agent-messages');
          container.scrollTop = container.scrollHeight;
        }
      }

      conversationHistory.push(
        { role: 'user', content: query },
        { role: 'assistant', content: finalAnswer }
      );

    } catch (err) {
      loadingEl.textContent = 'Error reaching agent backend.';
    }
  });

  /* ─── Repo re-detect (GitHub SPA) ─────────────────────────────── */
  const observer = new MutationObserver(() => detectRepo());
  observer.observe(document.head, {
    childList: true,
    subtree: true,
    characterData: true
  });
})();
