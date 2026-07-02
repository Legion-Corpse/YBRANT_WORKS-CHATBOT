(function () {
  "use strict";

  var script = document.currentScript;
  var API_BASE =
    (script && script.getAttribute("data-api")) || "http://localhost:8000/api";
  API_BASE = API_BASE.replace(/\/+$/, "");

  var BRAND = "YbrantWorks Assistant";
  var GREETING =
    "Hi! I'm the YbrantWorks assistant. Ask me about our services, the company, " +
    "careers, or how to get in touch — my answers come straight from our website.";
  var STARTER_CHIPS = [
    "What services do you offer?",
    "How can I contact you?",
    "About YbrantWorks"
  ];

  function ensureFonts() {
    if (document.getElementById("yb-chat-fonts")) return;
    var link = document.createElement("link");
    link.id = "yb-chat-fonts";
    link.rel = "stylesheet";
    link.href =
      "https://fonts.googleapis.com/css2?family=Comfortaa:wght@600&family=Poppins:wght@400;500;600&display=swap";
    document.head.appendChild(link);
  }

  function newId() {
    return (
      (window.crypto &&
        window.crypto.randomUUID &&
        window.crypto.randomUUID()) ||
      "s-" + Date.now() + "-" + Math.random().toString(36).slice(2)
    );
  }

  // Current session id, persisted for the browser tab. rotateSession() mints a
  // fresh one after an idle timeout so the next question starts a clean server
  // conversation (the backend also drops idle conversations after its own TTL).
  function sessionId() {
    try {
      var id = sessionStorage.getItem("yb-chat-session");
      if (!id) {
        id = newId();
        sessionStorage.setItem("yb-chat-session", id);
      }
      return id;
    } catch (e) {
      return "s-" + Date.now();
    }
  }

  function rotateSession() {
    var id = newId();
    try {
      sessionStorage.setItem("yb-chat-session", id);
    } catch (e) {}
    return id;
  }

  // Minutes of inactivity before the session resets. Override with
  // data-idle-minutes on the <script> tag; 0 disables the timeout.
  var IDLE_MINUTES = (function () {
    var v = script && parseFloat(script.getAttribute("data-idle-minutes"));
    return isNaN(v) ? 5 : v;
  })();

  var CSS = [
    ":host{all:initial}",
    "*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}",
    ":host{--red:#c00113;--red-dark:#a00113;--blue:#1976d2;--ink:#2b2b30;--gray:#5f5f65;",
    "--surface:#f4f4f6;--bg:#fff;--border:#e4e4e8;--amber:#8a5b00;--amber-bg:#fdf3e0;",
    "--font-ui:'Poppins',system-ui,sans-serif;--font-display:'Comfortaa',var(--font-ui);",
    "--z-panel:2147483000}",
    ".launcher{position:fixed;right:20px;bottom:20px;width:56px;height:56px;border:none;border-radius:50%;",
    "background:var(--red);color:#fff;cursor:pointer;box-shadow:0 4px 16px rgba(192,1,19,.32);",
    "display:flex;align-items:center;justify-content:center;z-index:var(--z-panel);",
    "transition:background .18s ease-out,transform .18s ease-out}",
    ".launcher:hover{background:var(--red-dark);transform:scale(1.04)}",
    ".launcher:focus-visible{outline:2px solid var(--red);outline-offset:3px}",
    ".launcher svg{width:26px;height:26px;fill:#fff}",
    ".panel{position:fixed;right:20px;bottom:90px;width:380px;max-width:calc(100vw - 40px);",
    "height:600px;max-height:calc(100vh - 120px);background:var(--bg);border-radius:16px;",
    "box-shadow:0 12px 48px rgba(20,20,25,.18);display:flex;flex-direction:column;overflow:hidden;",
    "z-index:var(--z-panel);font-family:var(--font-ui);",
    "transform-origin:bottom right;transition:transform .2s cubic-bezier(.22,1,.36,1),opacity .2s ease-out}",
    ".panel.hidden{transform:scale(.96) translateY(8px);opacity:0;pointer-events:none}",
    ".header{background:var(--red);color:#fff;padding:14px 16px;display:flex;align-items:center;gap:10px}",
    ".header .logo{width:34px;height:34px;border-radius:50%;background:rgba(255,255,255,.16);",
    "display:flex;align-items:center;justify-content:center;flex:none}",
    ".header .logo svg{width:18px;height:18px;fill:#fff}",
    ".header .title{font-family:var(--font-display);font-weight:600;font-size:15px;line-height:1.2}",
    ".header .sub{font-size:12px;opacity:.85;margin-top:2px}",
    ".log{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:10px;scroll-behavior:smooth}",
    ".msg{max-width:85%;padding:10px 14px;border-radius:12px;font-size:13.5px;line-height:1.55;",
    "white-space:pre-wrap;overflow-wrap:break-word;animation:rise .2s ease-out}",
    "@keyframes rise{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}",
    ".msg.bot{background:var(--surface);color:var(--ink);align-self:flex-start;border-bottom-left-radius:4px}",
    ".msg.user{background:var(--red);color:#fff;align-self:flex-end;border-bottom-right-radius:4px}",
    ".msg.bot strong{font-weight:600}",
    ".msg.bot a{color:var(--blue);font-weight:500;text-decoration:underline;text-underline-offset:2px}",
    ".msg.bot a:focus-visible{outline:2px solid var(--blue);outline-offset:1px}",
    ".tag{align-self:flex-start;font-size:11px;font-weight:500;color:var(--amber);background:var(--amber-bg);",
    "padding:2px 8px;border-radius:999px;margin-bottom:-4px}",
    ".divider{align-self:center;text-align:center;max-width:90%;font-size:11px;color:var(--gray);",
    "margin:4px 0;padding-top:8px;border-top:1px solid var(--border);width:100%}",
    ".sources{align-self:flex-start;max-width:85%;display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:-4px}",
    ".sources .label{font-size:11px;color:var(--gray)}",
    ".sources a,.sources .source{font-size:11.5px;text-decoration:none;border:1px solid var(--border);",
    "border-radius:999px;padding:2px 10px;background:var(--bg);transition:border-color .15s ease-out}",
    ".sources a{color:var(--blue)}",
    ".sources .source{color:var(--gray)}",
    ".sources a:hover{border-color:var(--blue);text-decoration:underline}",
    ".sources a:focus-visible{outline:2px solid var(--blue);outline-offset:1px}",
    ".chips{display:flex;flex-wrap:wrap;gap:8px;align-self:flex-start;max-width:90%}",
    ".chips button{font-family:var(--font-ui);font-size:12.5px;font-weight:500;color:var(--red);",
    "background:var(--bg);border:1px solid var(--red);border-radius:999px;padding:6px 14px;cursor:pointer;",
    "transition:background .15s ease-out,color .15s ease-out}",
    ".chips button:hover{background:var(--red);color:#fff}",
    ".chips button:focus-visible{outline:2px solid var(--red);outline-offset:2px}",
    ".typing{display:inline-flex;gap:5px;align-items:center;padding:14px}",
    ".typing span{width:7px;height:7px;border-radius:50%;background:var(--gray);opacity:.5;",
    "animation:wave 1.2s ease-in-out infinite}",
    ".typing span:nth-child(2){animation-delay:.15s}.typing span:nth-child(3){animation-delay:.3s}",
    "@keyframes wave{0%,60%,100%{transform:none;opacity:.5}30%{transform:translateY(-4px);opacity:1}}",
    ".composer{display:flex;gap:8px;padding:12px;border-top:1px solid var(--border)}",
    ".composer input{flex:1;font-family:var(--font-ui);font-size:13.5px;color:var(--ink);",
    "border:1px solid var(--border);border-radius:999px;padding:10px 16px;outline:none;",
    "transition:border-color .15s ease-out}",
    ".composer input::placeholder{color:#6b6b72}",
    ".composer input:focus{border-color:var(--red)}",
    ".composer input:disabled{background:var(--surface)}",
    ".composer .send{width:42px;height:42px;flex:none;border:none;border-radius:50%;background:var(--red);",
    "color:#fff;cursor:pointer;display:flex;align-items:center;justify-content:center;",
    "transition:background .15s ease-out}",
    ".composer .send:hover{background:var(--red-dark)}",
    ".composer .send:disabled{background:var(--border);cursor:default}",
    ".composer .send:focus-visible{outline:2px solid var(--red);outline-offset:2px}",
    ".composer .send svg{width:18px;height:18px;fill:#fff}",

    ".notice{font-size:11.5px;color:var(--gray);text-align:center;padding:4px 12px 10px}",
    ".notice.offline{color:var(--amber);background:var(--amber-bg);padding:8px 12px}",
    ".notice[hidden]{display:none}",
    "@media (max-width:480px){.panel{right:10px;left:10px;bottom:84px;width:auto;height:auto;",
    "top:10px;max-height:none}}",

    "@media (prefers-reduced-motion:reduce){.panel,.msg,.launcher,.chips button{transition:none;animation:none}",
    ".typing span{animation:none;opacity:.8}}"
  ].join("");

  var CHAT_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3C6.5 3 2 6.9 2 11.7c0 2.7 1.4 5.1 3.7 6.7-.2 1-.7 2.1-1.6 3.1-.2.2 0 .6.3.5 1.9-.3 3.5-1 4.7-1.8 1 .2 1.9.3 2.9.3 5.5 0 10-3.9 10-8.8S17.5 3 12 3z"/></svg>';
  var CLOSE_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12 19 6.4 17.6 5 12 10.6z"/></svg>';
  var SEND_ICON =
    '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 20.5 22 12 3 3.5V10l13 2-13 2z"/></svg>';

  function init() {
    ensureFonts();

    var host = document.createElement("div");
    host.id = "yb-chat-widget";
    document.body.appendChild(host);
    var root = host.attachShadow({ mode: "open" });

    var style = document.createElement("style");
    style.textContent = CSS;
    root.appendChild(style);

    var launcher = document.createElement("button");
    launcher.className = "launcher";
    launcher.setAttribute("aria-label", "Open chat with " + BRAND);
    launcher.setAttribute("aria-expanded", "false");
    launcher.innerHTML = CHAT_ICON;
    root.appendChild(launcher);

    var panel = document.createElement("div");
    panel.className = "panel hidden";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", BRAND);
    panel.innerHTML =
      '<div class="header">' +
      '<div class="logo">' +
      CHAT_ICON +
      "</div>" +
      '<div><div class="title">' +
      BRAND +
      "</div>" +
      '<div class="sub">Answers from ybrantworks.com</div></div>' +
      "</div>" +
      '<div class="log" role="log" aria-live="polite"></div>' +
      '<div class="notice offline" role="status" hidden></div>' +
      '<form class="composer">' +
      '<input type="text" placeholder="Ask about our services…" aria-label="Your question" maxlength="2000" autocomplete="off">' +
      '<button type="submit" class="send" aria-label="Send message">' +
      SEND_ICON +
      "</button>" +
      "</form>";
    root.appendChild(panel);

    var log = panel.querySelector(".log");
    var form = panel.querySelector(".composer");
    var input = form.querySelector("input");
    var sendBtn = form.querySelector(".send");
    var notice = panel.querySelector(".notice");

    var open = false;
    var busy = false;
    var offline = "onLine" in navigator ? !navigator.onLine : false;
    var greeted = false;

    var session = sessionId();
    var idleTimer = null;

    // Reset the inactivity clock on every interaction. If nothing happens for
    // IDLE_MINUTES the session ends (see onIdle).
    function resetIdle() {
      if (!IDLE_MINUTES || IDLE_MINUTES <= 0) return;
      if (idleTimer) clearTimeout(idleTimer);
      idleTimer = setTimeout(onIdle, IDLE_MINUTES * 60 * 1000);
    }

    // Idle timeout fired: mint a fresh session id so the next question starts
    // clean, and drop a divider so the visitor sees the break (existing messages
    // stay on screen). Re-greeting is intentionally skipped — the chat resumes,
    // it just no longer carries the earlier conversation's context.
    function onIdle() {
      idleTimer = null;
      session = rotateSession();
      addDivider(
        "Session ended after " +
          IDLE_MINUTES +
          " min of inactivity — your next message starts a new session."
      );
    }

    function setOpen(next) {
      open = next;
      panel.classList.toggle("hidden", !open);
      launcher.innerHTML = open ? CLOSE_ICON : CHAT_ICON;
      launcher.setAttribute(
        "aria-label",
        (open ? "Close" : "Open") + " chat with " + BRAND
      );
      launcher.setAttribute("aria-expanded", String(open));
      if (open) {
        if (!greeted) {
          greeted = true;
          addBot(GREETING);
          addChips(STARTER_CHIPS);
        }
        if (!offline) input.focus();
      } else {
        launcher.focus();
      }
    }

    launcher.addEventListener("click", function () {
      setOpen(!open);
    });
    panel.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && open) setOpen(false);
    });

    function scrollDown() {
      log.scrollTop = log.scrollHeight;
    }

    function addMsg(text, who) {
      var el = document.createElement("div");
      el.className = "msg " + who;
      if (who === "bot") {
        renderBotMessage(el, text);
      } else {
        el.textContent = text;
      }
      log.appendChild(el);
      scrollDown();
      return el;
    }

    function addDivider(text) {
      var el = document.createElement("div");
      el.className = "divider";
      el.setAttribute("role", "separator");
      el.textContent = text;
      log.appendChild(el);
      scrollDown();
    }

    function addBot(text) {
      return addMsg(text, "bot");
    }

    function renderBotMessage(parent, text) {
      var pattern =
        /\[([^\]]+)\]\(([^\s)]+)\)|(https?:\/\/[^\s<]+)|([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})|\*\*([^*]+)\*\*/gi;
      var lastIndex = 0;
      var match;

      while ((match = pattern.exec(text)) !== null) {
        appendPlainText(parent, text.slice(lastIndex, match.index));
        if (match[1] && match[2]) {
          appendMarkdownLink(parent, match[0], match[1], match[2]);
        } else if (match[3]) {
          appendAutoLink(parent, match[3]);
        } else if (match[4]) {
          appendLink(parent, match[4], "mailto:" + match[4]);
        } else if (match[5]) {
          var strong = document.createElement("strong");
          strong.textContent = match[5];
          parent.appendChild(strong);
        }
        lastIndex = pattern.lastIndex;
      }

      appendPlainText(parent, text.slice(lastIndex));
    }

    function appendPlainText(parent, text) {
      if (!text) return;
      var lines = text.split("\n");
      lines.forEach(function (line, index) {
        if (index > 0) parent.appendChild(document.createElement("br"));
        if (line) parent.appendChild(document.createTextNode(line));
      });
    }

    function appendMarkdownLink(parent, original, label, url) {
      if (isSafeHref(url)) {
        appendLink(parent, label, url);
      } else {
        appendPlainText(parent, original);
      }
    }

    function appendLink(parent, label, url) {
      var a = document.createElement("a");
      a.href = url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = label;
      parent.appendChild(a);
    }

    function isSafeHref(url) {
      return /^(https?:\/\/|mailto:|tel:|\/)/i.test(url);
    }

    function appendAutoLink(parent, rawUrl) {
      var url = rawUrl;
      var trailing = "";
      while (/[.,!?;:]$/.test(url)) {
        trailing = url.slice(-1) + trailing;
        url = url.slice(0, -1);
      }
      appendLink(parent, url, url);
      appendPlainText(parent, trailing);
    }

    function addSources(sources) {
      if (!sources || !sources.length) return;
      var wrap = document.createElement("div");
      wrap.className = "sources";
      var label = document.createElement("span");
      label.className = "label";
      label.textContent = "Sources:";
      wrap.appendChild(label);
      sources.forEach(function (s) {
        if (/^https?:\/\//.test(s.url)) {
          var a = document.createElement("a");
          a.href = s.url;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          a.textContent = s.title || s.url;
          wrap.appendChild(a);
        } else {
          var source = document.createElement("span");
          source.className = "source";
          source.textContent = s.title || s.url;
          wrap.appendChild(source);
        }
      });
      log.appendChild(wrap);
      scrollDown();
    }

    function addChips(labels) {
      if (!labels || !labels.length) return;
      var wrap = document.createElement("div");
      wrap.className = "chips";
      labels.forEach(function (label) {
        var b = document.createElement("button");
        b.type = "button";
        b.textContent = label;
        b.addEventListener("click", function () {
          wrap.remove();
          send(label);
        });
        wrap.appendChild(b);
      });
      log.appendChild(wrap);
      scrollDown();
    }

    function showTyping() {
      var el = document.createElement("div");
      el.className = "msg bot typing";
      el.setAttribute("aria-label", "Assistant is typing");
      el.innerHTML = "<span></span><span></span><span></span>";
      log.appendChild(el);
      scrollDown();
      return el;
    }

    function setBusy(next) {
      busy = next;
      updateComposer();
    }

    function setOffline(next) {
      offline = next;
      notice.hidden = !offline;
      notice.textContent = offline
        ? "You're offline — reconnect to ask a question."
        : "";
      updateComposer();
    }

    function updateComposer() {
      input.disabled = busy || offline;
      sendBtn.disabled = busy || offline;
      if (!busy && !offline && open) input.focus();
    }

    window.addEventListener("online", function () {
      setOffline(false);
    });
    window.addEventListener("offline", function () {
      setOffline(true);
    });
    setOffline(offline);

    function setBotText(el, text) {
      el.textContent = "";  // clear prior children (no untrusted HTML)
      renderBotMessage(el, text);
      scrollDown();
    }

    function send(text) {
      text = (text || "").trim();
      if (!text || busy || offline) return;
      resetIdle(); // sending is activity — restart the inactivity clock
      addMsg(text, "user");
      setBusy(true);
      var typing = showTyping();

      streamChat(text, typing)
        .catch(function () {
          // Stream unavailable (or failed before any token) — retry the
          // buffered endpoint so the user still gets an answer.
          return sendBuffered(text, typing);
        })
        .then(function () {
          setBusy(false);
          resetIdle(); // answer received — count idle from the last interaction
        });
    }

    // SSE: render tokens as they arrive. Resolves once the stream ends; rejects
    // ONLY if it fails before the first token (so the caller can safely fall
    // back without double-answering once content is already on screen).
    function streamChat(text, typing) {
      return fetch(API_BASE + "/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: session, message: text })
      }).then(function (res) {
        if (!res.ok || !res.body || !res.body.getReader) {
          throw new Error("stream unavailable");
        }
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        var sseBuf = "";
        var bubble = null;
        var answer = "";

        function ensureBubble() {
          if (!bubble) {
            typing.remove();
            bubble = addBot("");
          }
        }

        function handle(ev, data) {
          if (ev === "token") {
            ensureBubble();
            answer += data.text || "";
            setBotText(bubble, answer);
          } else if (ev === "replace" || ev === "error") {
            ensureBubble();
            answer = data.answer || "";
            setBotText(bubble, answer);
          } else if (ev === "meta") {
            addSources(data.sources);
            addChips(data.suggestions);
          }
        }

        function pump() {
          return reader.read().then(function (r) {
            if (r.done) {
              if (!bubble) typing.remove();
              return;
            }
            sseBuf += decoder.decode(r.value, { stream: true });
            var frames = sseBuf.split("\n\n");
            sseBuf = frames.pop();
            frames.forEach(function (frame) {
              var ev = null;
              var dataStr = null;
              frame.split("\n").forEach(function (line) {
                if (line.indexOf("event: ") === 0) ev = line.slice(7);
                else if (line.indexOf("data: ") === 0) dataStr = line.slice(6);
              });
              if (ev && dataStr != null) {
                try {
                  handle(ev, JSON.parse(dataStr));
                } catch (e) {
                  /* ignore malformed frame */
                }
              }
            });
            return pump();
          });
        }

        return pump().catch(function (err) {
          // Mid-stream failure after content shown: stop quietly. Before any
          // token: re-throw so send() falls back to the buffered endpoint.
          if (!bubble) throw err;
        });
      });
    }

    function sendBuffered(text, typing) {
      return fetch(API_BASE + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: session, message: text })
      })
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.json();
        })
        .then(function (data) {
          typing.remove();
          addBot(data.answer);
          addSources(data.sources);
          addChips(data.suggestions);
        })
        .catch(function () {
          typing.remove();
          addBot(
            "Sorry — I couldn't reach the server. Please try again in a moment, " +
              "or email info@ybrantworks.com."
          );
        });
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var text = input.value;
      input.value = "";
      send(text);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
