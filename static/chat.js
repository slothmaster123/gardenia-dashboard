/**
 * Gardenia Dashboard – Chat module
 *
 * Responsibilities:
 *   1. Load profile list from /api/chat/profiles and render the sidebar.
 *   2. Persist message history to localStorage (one key per profile).
 *   3. Send messages via /api/chat/send (polling-based; the backend
 *      uses WebSocket for the default profile and CLI for others).
 *   4. Basic HTML escaping to prevent XSS.
 *   5. Show model/provider info per profile.
 */

(function () {
  "use strict";

  // ── Constants ──────────────────────────────────────────────────────────
  const LS_PREFIX = "gardenia_chat_messages_";
  const PROFILES_URL = "/api/chat/profiles";
  const SEND_URL = "/api/chat/send";
  const STATUS_URL = "/api/chat/status";

  // ── DOM refs ───────────────────────────────────────────────────────────
  const profileList = document.getElementById("profile-list");
  const messageList = document.getElementById("message-list");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const chatSend = document.getElementById("chat-send");

  // ── State ──────────────────────────────────────────────────────────────
  let profiles = [];
  let activeProfileId = null;
  /** Map<profileId, {role: 'user'|'assistant', text: string}[]> */
  let messagesByProfile = {};
  let sending = false;

  // ── Helpers ────────────────────────────────────────────────────────────

  /** Basic HTML escaping. */
  function esc(str) {
    const div = document.createElement("div");
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  /** Simple markdown-to-HTML for code blocks and bold. */
  function simpleMarkdown(text) {
    // Code blocks ```
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, function (_, lang, code) {
      return '<pre class="bubble-code"><code>' + esc(code.trim()) + "</code></pre>";
    });
    // Inline code `...`
    text = text.replace(/`([^`]+)`/g, '<code class="bubble-inline">$1</code>');
    // Bold **...**
    text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    return text;
  }

  /** localStorage key for a profile. */
  function lsKey(profileId) {
    return LS_PREFIX + profileId;
  }

  /** Load messages for a profile from localStorage. */
  function loadMessages(profileId) {
    try {
      const raw = localStorage.getItem(lsKey(profileId));
      return raw ? JSON.parse(raw) : [];
    } catch (_) {
      return [];
    }
  }

  /** Persist messages for a profile to localStorage. */
  function saveMessages(profileId, msgs) {
    try {
      localStorage.setItem(lsKey(profileId), JSON.stringify(msgs));
    } catch (_) {
      // localStorage full or unavailable — fail silently.
    }
  }

  /** Get the display name for the active profile. */
  function activeProfileName() {
    if (!activeProfileId) return "Assistant";
    const p = profiles.find(function (pr) { return pr.id === activeProfileId; });
    return p ? p.name : activeProfileId;
  }

  /** Append a message bubble to the message list. */
  function appendBubble(role, text, rawText) {
    // Remove typing indicator if present
    var typingEl = messageList.querySelector(".typing-indicator");
    if (typingEl) typingEl.remove();

    var div = document.createElement("div");
    div.className = "message-bubble " + role;

    var roleLabel = role === "user" ? "You" : activeProfileName();
    var contentHtml = role === "user" ? esc(text) : simpleMarkdown(text);

    div.innerHTML =
      '<div class="bubble-role">' + esc(roleLabel) + "</div>" +
      '<div class="bubble-text">' + contentHtml + "</div>";
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
  }

  /** Show a typing indicator. */
  function showTyping() {
    var existing = messageList.querySelector(".typing-indicator");
    if (existing) return;
    var div = document.createElement("div");
    div.className = "message-bubble assistant typing-indicator";
    div.innerHTML =
      '<div class="bubble-role">' + esc(activeProfileName()) + "</div>" +
      '<div class="bubble-text typing-dots"><span>.</span><span>.</span><span>.</span></div>';
    messageList.appendChild(div);
    messageList.scrollTop = messageList.scrollHeight;
  }

  /** Re-render the entire message list for the active profile. */
  function renderMessages() {
    messageList.innerHTML = "";

    if (!activeProfileId) {
      messageList.innerHTML =
        '<div class="empty-chat"><div class="icon">☾</div><div class="muted">Select a profile and start chatting.</div></div>';
      return;
    }

    var msgs = messagesByProfile[activeProfileId] || [];
    if (msgs.length === 0) {
      // Show profile info
      var p = profiles.find(function (pr) { return pr.id === activeProfileId; });
      var modelInfo = p ? " · " + esc(p.provider + " / " + p.model) : "";
      messageList.innerHTML =
        '<div class="empty-chat">' +
        '<div class="icon">☾</div>' +
        '<div class="muted"><strong>' + esc(activeProfileName()) + '</strong>' + modelInfo + '</div>' +
        '<div class="muted" style="margin-top:6px">Send a message to begin.</div>' +
        "</div>";
      return;
    }

    for (var i = 0; i < msgs.length; i++) {
      appendBubble(msgs[i].role, msgs[i].text);
    }
  }

  /** Enable or disable the input bar based on whether a profile is selected. */
  function updateInputState() {
    if (activeProfileId && !sending) {
      chatInput.disabled = false;
      chatSend.disabled = false;
      chatInput.placeholder = "Message " + activeProfileName() + "…";
      chatInput.focus();
    } else if (sending) {
      chatInput.disabled = true;
      chatSend.disabled = true;
      chatSend.textContent = "…";
    } else {
      chatInput.disabled = true;
      chatSend.disabled = true;
      chatInput.placeholder = "Select a profile first";
      chatSend.textContent = "Send";
    }
  }

  // ── Profile selection ──────────────────────────────────────────────────

  function selectProfile(profileId) {
    activeProfileId = profileId;

    // Highlight sidebar item
    var items = profileList.querySelectorAll(".profile-item");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("active", items[i].dataset.profileId === profileId);
    }

    // Load history (already cached in messagesByProfile from sidebar render)
    if (!messagesByProfile[profileId]) {
      messagesByProfile[profileId] = loadMessages(profileId);
    }

    renderMessages();
    updateInputState();
  }

  // ── Sidebar rendering ──────────────────────────────────────────────────

  function renderSidebar(profileData) {
    profileList.innerHTML = "";
    profiles = profileData;

    for (var i = 0; i < profiles.length; i++) {
      var p = profiles[i];
      var li = document.createElement("li");
      li.className = "profile-item";
      li.dataset.profileId = p.id;

      var badgeClass = p.id === "default" ? "profile-badge badge-default" : "profile-badge";
      var modelShort = (p.model || "").length > 28 ? p.model.substring(0, 25) + "…" : (p.model || "");

      li.innerHTML =
        '<div class="profile-name">' + esc(p.name) +
        '  <span class="' + badgeClass + '">' + esc(modelShort) + "</span>" +
        "</div>" +
        '<div class="profile-role">' + esc(p.role || "") + "</div>";

      li.addEventListener("click", (function (pid) {
        return function () { selectProfile(pid); };
      })(p.id));

      profileList.appendChild(li);

      // Pre-load messages from localStorage
      if (!messagesByProfile[p.id]) {
        messagesByProfile[p.id] = loadMessages(p.id);
      }
    }
  }

  // ── API calls ──────────────────────────────────────────────────────────

  async function fetchProfiles() {
    try {
      var resp = await fetch(PROFILES_URL);
      if (!resp.ok) throw new Error("HTTP " + resp.status);
      var data = await resp.json();
      renderSidebar(data.profiles || []);
    } catch (err) {
      profileList.innerHTML =
        '<li class="profile-item muted">Failed to load profiles: ' + esc(err.message) + "</li>";
      console.error("chat: profile fetch error", err);
    }
  }

  /**
   * Send a message with timeout.
   * Uses AbortController to enforce a max wait time.
   */
  async function sendMessage(profileId, text) {
    var controller = new AbortController();
    var timeoutId = setTimeout(function () { controller.abort(); }, 90000); // 90s max

    try {
      var resp = await fetch(SEND_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: profileId, message: text }),
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      if (!resp.ok) {
        var body = await resp.text();
        return { ok: false, error: "Server error " + resp.status + ": " + body.substring(0, 200) };
      }

      return await resp.json();
    } catch (err) {
      clearTimeout(timeoutId);
      if (err.name === "AbortError") {
        return { ok: false, error: "Request timed out after 90s. The AI model may be slow — try a shorter message." };
      }
      return { ok: false, error: "Network error: " + err.message };
    }
  }

  // ── Form submission ────────────────────────────────────────────────────

  chatForm.addEventListener("submit", async function (e) {
    e.preventDefault();

    if (sending) return;

    var text = chatInput.value.trim();
    if (!text || !activeProfileId) return;

    // Disable input while waiting
    sending = true;
    updateInputState();

    // Add user message to state + UI
    if (!messagesByProfile[activeProfileId]) {
      messagesByProfile[activeProfileId] = [];
    }
    messagesByProfile[activeProfileId].push({ role: "user", text: text });
    appendBubble("user", text);
    saveMessages(activeProfileId, messagesByProfile[activeProfileId]);
    chatInput.value = "";

    // Show typing indicator
    showTyping();

    // Send to backend
    var result;
    try {
      result = await sendMessage(activeProfileId, text);
    } catch (err) {
      result = { ok: false, error: "Network error: " + err.message };
    }

    // Remove typing indicator
    var typingEl = messageList.querySelector(".typing-indicator");
    if (typingEl) typingEl.remove();

    // Process response
    if (result.ok) {
      var replyText = result.reply || "(empty reply)";
      messagesByProfile[activeProfileId].push({ role: "assistant", text: replyText });
      appendBubble("assistant", replyText);
      saveMessages(activeProfileId, messagesByProfile[activeProfileId]);
    } else {
      var errText = "⚠️ " + (result.error || "Unknown error");
      messagesByProfile[activeProfileId].push({ role: "assistant", text: errText });
      appendBubble("assistant", errText);
      saveMessages(activeProfileId, messagesByProfile[activeProfileId]);
    }

    // Re-enable input
    sending = false;
    updateInputState();
    chatInput.focus();
  });

  // ── Init ───────────────────────────────────────────────────────────────

  fetchProfiles();
})();
