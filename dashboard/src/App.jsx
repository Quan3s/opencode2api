import React, { useEffect, useRef, useState } from "react";
import {
  BotIcon,
  ChatIcon,
  CheckIcon,
  CloseIcon,
  CopyIcon,
  Logo,
  RefreshIcon,
  SendIcon,
  SettingsIcon,
  ShieldIcon,
  BoltIcon,
  ToolIcon,
  TrashIcon,
  UserIcon,
} from "./icons.jsx";

const STORAGE_KEY = "o2a.dashboard.settings.v1";
const DEFAULT_SETTINGS = {
  baseUrl: "",
  apiKey: "demo-local-key",
  model: "big-pickle",
  system: "",
  stream: true,
  tools: true,
};

const CALC_TOOL = {
  type: "function",
  function: {
    name: "calculator",
    description: "Tính toán biểu thức toán học cơ bản. Truyền expression, ví dụ '2+2*3'.",
    parameters: {
      type: "object",
      properties: {
        expression: { type: "string", description: "Biểu thức toán học cần tính" },
      },
      required: ["expression"],
      additionalProperties: false,
    },
    strict: false,
  },
};

const uid = () =>
  globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    return { ...DEFAULT_SETTINGS, ...saved };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

function safeEval(expression) {
  if (typeof expression !== "string" || !/^[\d+\-*/().\s]+$/.test(expression)) return null;
  try {
    // eslint-disable-next-line no-new-func
    const value = Function(`"use strict"; return (${expression});`)();
    return Number.isFinite(value) ? String(value) : null;
  } catch {
    return null;
  }
}

function mergeToolCalls(previous, deltas) {
  const calls = Array.isArray(previous) ? [...previous] : [];
  for (const delta of deltas || []) {
    const index = Number.isInteger(delta.index) ? delta.index : 0;
    const base = calls[index] || { id: "", type: "function", function: { name: "", arguments: "" } };
    calls[index] = {
      ...base,
      ...delta,
      function: {
        ...(base.function || {}),
        ...(delta.function || {}),
      },
    };
  }
  return calls;
}

function toWire(message) {
  const out = { role: message.role, content: message.content || "" };
  if (message.tool_calls) out.tool_calls = message.tool_calls;
  if (message.tool_call_id) out.tool_call_id = message.tool_call_id;
  if (message.name) out.name = message.name;
  return out;
}

function renderText(text) {
  if (!text) return null;
  return String(text)
    .split(/(\*\*[^*]+\*\*)/g)
    .map((part, index) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={index}>{part.slice(2, -2)}</strong>
      ) : (
        <React.Fragment key={index}>{part}</React.Fragment>
      ),
    );
}

function ToolCard({ call, result, toolInput, onInput, onSubmit }) {
  const name = call.function?.name || "tool";
  let args = call.function?.arguments || "";
  try {
    args = JSON.stringify(JSON.parse(args), null, 2);
  } catch {
    /* keep raw */
  }
  return (
    <div className="tool-card">
      <div className="tool-head">
        <span className="tool-badge"><ToolIcon size={15} /> tool_call</span>
        <span className="tool-name">{name}</span>
      </div>
      <pre className="tool-args">{args || "{}"}</pre>
      {result != null ? (
        <div className="tool-result"><CheckIcon size={14} /> result: {String(result)}</div>
      ) : (
        <div className="tool-input-row">
          <input
            value={toolInput || ""}
            onChange={(event) => onInput(event.target.value)}
            placeholder="Kết quả tool (nếu tool không tự động chạy)..."
          />
          <button type="button" onClick={onSubmit}>Gửi kết quả</button>
        </div>
      )}
    </div>
  );
}

export default function App() {
  const [settings, setSettings] = useState(loadSettings);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("ready");
  const [showSettings, setShowSettings] = useState(false);
  const [models, setModels] = useState(["big-pickle", "mimo-v2.6-flash-free"]);
  const [toolInputs, setToolInputs] = useState({});
  const [copied, setCopied] = useState(null);
  const settingsRef = useRef(settings);
  const abortRef = useRef(null);

  useEffect(() => {
    settingsRef.current = settings;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  }, [settings]);

  const base = () => (settingsRef.current.baseUrl || window.location.origin).replace(/\/$/, "");
  const headers = () => ({
    "Content-Type": "application/json",
    Authorization: `Bearer ${settingsRef.current.apiKey}`,
  });

  useEffect(() => {
    let alive = true;
    fetch(`${base()}/v1/models`, { headers: headers() })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (alive && data?.data?.length) {
          setModels(Array.from(new Set(data.data.map((item) => item.id))));
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [settings.baseUrl, settings.apiKey]);

  async function callBridge(history, depth = 0) {
    const current = settingsRef.current;
    if (!history.length || depth > 3) return;
    const assistantId = uid();
    setMessages((items) => [
      ...items,
      { id: assistantId, role: "assistant", content: "", tool_calls: null, pending: true },
    ]);
    setBusy(true);
    setStatus("thinking");

    const wireMessages = history.map(toWire);
    if (current.system && !wireMessages.some((item) => item.role === "system")) {
      wireMessages.unshift({ role: "system", content: current.system });
    }
    const payload = {
      model: current.model,
      messages: wireMessages,
      stream: current.stream,
    };
    if (current.tools) payload.tools = [CALC_TOOL];

    const controller = new AbortController();
    abortRef.current = controller;

    const update = (content, toolCalls, pending = true) =>
      setMessages((items) =>
        items.map((item) =>
          item.id === assistantId
            ? { ...item, content, tool_calls: toolCalls, pending }
            : item,
        ),
      );

    try {
      const response = await fetch(`${base()}/v1/chat/completions`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
      if (!response.ok) {
        const text = await response.text();
        let message = text;
        try {
          message = JSON.parse(text)?.error?.message || text;
        } catch {
          /* keep raw */
        }
        throw new Error(`HTTP ${response.status}: ${message}`);
      }

      let content = "";
      let toolCalls = null;

      if (current.stream && response.body) {
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() || "";
          for (const frame of frames) {
            for (const line of frame.split(/\r?\n/)) {
              if (!line.startsWith("data:")) continue;
              const raw = line.slice(5).trim();
              if (!raw || raw === "[DONE]") continue;
              let event;
              try {
                event = JSON.parse(raw);
              } catch {
                continue;
              }
              const delta = event.choices?.[0]?.delta || {};
              if (delta.content) content += String(delta.content);
              if (delta.tool_calls) toolCalls = mergeToolCalls(toolCalls, delta.tool_calls);
              update(content, toolCalls);
            }
          }
        }
      } else {
        const data = await response.json();
        const message = data.choices?.[0]?.message || {};
        content = message.content || "";
        toolCalls = message.tool_calls || null;
      }

      update(content, toolCalls, false);
      setStatus("done");

      if (toolCalls?.length && depth < 3) {
        const toolMessages = [];
        let needsManual = false;
        for (const call of toolCalls) {
          const name = call.function?.name || "";
          let result = null;
          if (name === "calculator") {
            let args = {};
            try {
              args = JSON.parse(call.function?.arguments || "{}");
            } catch {
              /* ignore */
            }
            result = safeEval(args.expression);
            if (result == null) result = "invalid expression";
          } else {
            result = toolInputs[call.id] || "";
            if (!result) needsManual = true;
          }
          toolMessages.push({
            id: uid(),
            role: "tool",
            name,
            tool_call_id: call.id,
            content: result,
          });
        }
        if (!needsManual) {
          setMessages((items) => [...items, ...toolMessages]);
          const assistantWire = {
            role: "assistant",
            content,
            tool_calls: toolCalls,
          };
          await callBridge(
            [...history, assistantWire, ...toolMessages.map(toWire)],
            depth + 1,
          );
        } else {
          setMessages((items) => [...items, ...toolMessages.filter((item) => item.content)]);
        }
      }
    } catch (error) {
      if (error.name === "AbortError") {
        update(content, toolCalls, false);
        setStatus("stopped");
      } else {
        update(`⚠️ ${error.message}`, null, false);
        setStatus("error");
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  }

  function submit() {
    const text = input.trim();
    if (!text || busy) return;
    const userMessage = { id: uid(), role: "user", content: text };
    const next = [...messages, userMessage];
    setMessages(next);
    setInput("");
    callBridge(next);
  }

  function submitManualTool(call) {
    const value = toolInputs[call.id] || "";
    const index = messages.findIndex((item) =>
      item.tool_calls?.some((candidate) => candidate.id === call.id),
    );
    if (index < 0) return;
    const history = messages.slice(0, index + 1).map(toWire);
    const toolMessage = {
      id: uid(),
      role: "tool",
      name: call.function?.name || "tool",
      tool_call_id: call.id,
      content: value,
    };
    setMessages((items) => [...items, toolMessage]);
    callBridge([...history, toWire(toolMessage)], 2);
  }

  function copy(text) {
    navigator.clipboard?.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 1200);
  }

  const statusLabel = {
    ready: "Sẵn sàng",
    thinking: "Đang sinh...",
    done: "Hoàn tất",
    stopped: "Đã dừng",
    error: "Lỗi",
  }[status];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Logo size={24} /></span>
          <div>
            <strong>opencode2openai</strong>
            <small>OpenAI-compatible bridge</small>
          </div>
        </div>

        <nav className="nav">
          <button className="nav-item active"><ChatIcon size={18} /> Chat</button>
          <button className="nav-item" onClick={() => setShowSettings(true)}>
            <SettingsIcon size={18} /> Cài đặt
          </button>
        </nav>

        <div className="sidebar-spacer" />

        <button className="new-chat" onClick={() => setMessages([])}>
          <RefreshIcon size={17} /> Cuộc trò chuyện mới
        </button>

        <div className="status-card">
          <span className={`status-dot ${status}`} />
          <div>
            <strong>{statusLabel}</strong>
            <small>{settings.model}</small>
          </div>
          <ShieldIcon size={17} className="muted" />
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>Trợ lý OpenCode</h1>
            <p>Chat OpenAI API · generate thật qua free tier</p>
          </div>
          <div className="top-actions">
            <select
              value={settings.model}
              onChange={(event) => setSettings({ ...settings, model: event.target.value })}
              aria-label="Model"
            >
              {models.map((model) => <option key={model} value={model}>{model}</option>)}
            </select>
            <button className="icon-btn" onClick={() => setShowSettings(true)} title="Cài đặt">
              <SettingsIcon size={18} />
            </button>
          </div>
        </header>

        <section className="chat-scroll">
          {messages.length === 0 ? (
            <div className="empty">
              <span className="empty-icon"><BoltIcon size={34} /></span>
              <h2>Bắt đầu trò chuyện</h2>
              <p>Gửi một prompt để thấy bridge sinh text thật từ OpenCode.</p>
              <div className="suggestions">
                {["Chào bạn tên là gì?", "Chỉ trả lời đúng chữ OK.", "Tính 12*(3+4) dùng tool"].map((prompt) => (
                  <button key={prompt} onClick={() => setInput(prompt)}>{prompt}</button>
                ))}
              </div>
            </div>
          ) : (
            <div className="message-list">
              {messages.map((message) => {
                if (message.role === "tool") {
                  return (
                    <div key={message.id} className="tool-message">
                      <ToolIcon size={15} /> <strong>{message.name || "tool"}</strong>
                      <span> → {String(message.content)}</span>
                    </div>
                  );
                }
                const isUser = message.role === "user";
                return (
                  <div key={message.id} className={`message ${isUser ? "user" : "assistant"}`}>
                    <span className={`avatar ${isUser ? "user" : "bot"}`}>
                      {isUser ? <UserIcon size={17} /> : <BotIcon size={17} />}
                    </span>
                    <div className="bubble">
                      <div className="bubble-meta">
                        {isUser ? "Bạn" : settings.model}
                        {message.pending && <span className="typing">đang viết…</span>}
                      </div>
                      <div className="bubble-content">
                        {renderText(message.content)}
                        {!message.content && message.pending && <span className="cursor" />}
                      </div>
                      {message.tool_calls?.map((call, index) => {
                        const result = messages.find(
                          (item) => item.role === "tool" && item.tool_call_id === call.id,
                        )?.content;
                        return (
                          <ToolCard
                            key={call.id || index}
                            call={call}
                            result={result}
                            toolInput={toolInputs[call.id]}
                            onInput={(value) => setToolInputs({ ...toolInputs, [call.id]: value })}
                            onSubmit={() => submitManualTool(call)}
                          />
                        );
                      })}
                      {!isUser && message.content && (
                        <button className="copy-btn" onClick={() => copy(message.content)} title="Copy">
                          {copied === message.content ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <footer className="composer-wrap">
          <div className="composer">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              placeholder="Nhắn tin… Enter để gửi, Shift+Enter để xuống dòng"
              rows={2}
            />
            {busy ? (
              <button className="send stop" onClick={() => abortRef.current?.abort()} title="Dừng">
                <CloseIcon size={18} />
              </button>
            ) : (
              <button className="send" onClick={submit} disabled={!input.trim()} title="Gửi">
                <SendIcon size={18} />
              </button>
            )}
          </div>
          <div className="composer-hint">
            <span><ShieldIcon size={13} /> key được lưu trong trình duyệt</span>
            <span><ToolIcon size={13} /> {settings.tools ? "tool calls bật" : "tool calls tắt"}</span>
          </div>
        </footer>
      </main>

      {showSettings && (
        <div className="modal-backdrop" onClick={() => setShowSettings(false)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h2>Cài đặt</h2>
              <button className="icon-btn" onClick={() => setShowSettings(false)}>
                <CloseIcon size={18} />
              </button>
            </div>
            <label>
              Bridge base URL
              <input
                value={settings.baseUrl}
                onChange={(event) => setSettings({ ...settings, baseUrl: event.target.value })}
                placeholder="https://preview-xxx.up.railway.app"
              />
            </label>
            <label>
              API key
              <input
                type="password"
                value={settings.apiKey}
                onChange={(event) => setSettings({ ...settings, apiKey: event.target.value })}
              />
            </label>
            <label>
              Model mặc định
              <input
                value={settings.model}
                onChange={(event) => setSettings({ ...settings, model: event.target.value })}
              />
            </label>
            <label>
              System prompt
              <textarea
                rows={3}
                value={settings.system}
                onChange={(event) => setSettings({ ...settings, system: event.target.value })}
                placeholder="Để trống để dùng prompt mặc định của CLI"
              />
            </label>
            <div className="toggles">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={settings.stream}
                  onChange={(event) => setSettings({ ...settings, stream: event.target.checked })}
                />
                <span>Streaming SSE</span>
              </label>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={settings.tools}
                  onChange={(event) => setSettings({ ...settings, tools: event.target.checked })}
                />
                <span>Gửi tool definitions</span>
              </label>
            </div>
            <div className="modal-foot">
              <button className="primary" onClick={() => { fetchModelsNow(); setShowSettings(false); }}>
                Lưu & đóng
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );

  function fetchModelsNow() {
    fetch(`${base()}/v1/models`, { headers: headers() })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (data?.data?.length) setModels(Array.from(new Set(data.data.map((item) => item.id))));
      })
      .catch(() => {});
  }
}
