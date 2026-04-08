// Persona Agents — main.js
// Standalone plugin view: delegation chain with persona-powered agents
// Auto-loaded by Sapphire. Injects nav item + registers view.
// Pattern follows Round Table architecture.

import { registerView, switchView } from '/static/core/router.js';
import * as eventBus from '/static/core/event-bus.js';
import * as audio from '/static/audio.js';

const CSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
const API = '/api/plugin/persona-agents';

// ─── Plugin entry point ─────────────────────────────────────────────────────

export default {
    init() {
        _injectNav();
        _createViewContainer();
        registerView('persona-agents', {
            init: (el) => _initView(el),
            show: () => _startListening(),
            hide: () => _stopListening(),
        });
        _setupToastNotifications();

        // TTS state — always listen, even when view is hidden
        eventBus.on('tts_playing', () => {
            _ttsPlaying = true;
            _updateBarTtsState();
        });
        eventBus.on('tts_stopped', () => {
            _ttsPlaying = false;
            _updateBarTtsState();
        });
    }
};

// ─── State ─────────────────────────────────────────────────────────────────

let _container = null;
let _allPersonas = [];
let _ttsPlayer = null;  // Legacy — kept for compat, use _ttsPlaying instead
let _selectedChat = '';           // '' = current active chat
let _viewVisible = false;
let _unsubscribers = [];         // Event listener cleanup
let _autoContinue = false;       // When ON, auto-nudge lead persona after delegate completes
let _userAvatarUrl = '/static/users/user.webp';  // Fallback default

// ─── Live Streaming State ──────────────────────────────────────────────────
// Instead of fetching merged history, we track assistant turns live via SSE
// events and build per-segment timeline entries with accurate timestamps.

let _liveSegments = [];          // Segments for the CURRENT turn: [{kind, text, timestamp, ...}]
let _completedTurns = new Map(); // turnId -> {segments: [...], mergedTimestamp: '...'}
let _liveAccumulator = '';       // Text buffer for current content segment
let _liveSegmentStart = null;    // ISO timestamp when current segment started
let _inLiveTurn = false;         // Are we in an active assistant turn?
let _liveTurnId = 0;             // Incrementing turn counter
let _livePreDelegation = true;   // Are we before or after delegation in this turn?

// ─── Navigation ─────────────────────────────────────────────────────────────

function _injectNav() {
    const rail = document.getElementById('nav-rail');
    if (!rail) return;
    const spacer = rail.querySelector('.nav-spacer');

    const btn = document.createElement('button');
    btn.className = 'nav-item';
    btn.dataset.view = 'persona-agents';
    btn.innerHTML = '<span class="nav-icon">\u{1F3AD}</span><span class="nav-label">Agents</span>';
    if (spacer) rail.insertBefore(btn, spacer);
    else rail.appendChild(btn);
}

function _createViewContainer() {
    const app = document.getElementById('app-content');
    if (!app) return;
    const div = document.createElement('div');
    div.id = 'view-persona-agents';
    div.className = 'view';
    div.style.display = 'none';
    app.appendChild(div);
}

// ─── Init ───────────────────────────────────────────────────────────────────

function _initView(el) {
    _container = el;
    _injectStyles();
    el.innerHTML = `<div class="pa-root">${_buildLayout()}</div>`;
    _bindEvents(el);
    _loadPersonas();
    _loadChatSelector();
    _loadAutoContinueSetting();
    _loadUserAvatar();
    _updateContextBar();
}

function _buildLayout() {
    return `
        <header class="pa-header">
            <h1 class="pa-title">\u{1F3AD} Persona Agents</h1>
            <div class="pa-chat-selector">
                <button class="pa-btn pa-chat-toggle" id="pa-chat-toggle" title="Switch chat">
                    <span id="pa-chat-label">Loading...</span> \u25BC
                </button>
                <div class="pa-chat-dropdown" id="pa-chat-dropdown" style="display:none">
                    <div class="pa-chat-dd-header">
                        <span class="pa-chat-dd-title">Chats</span>
                        <button class="pa-btn pa-btn-sm" id="pa-chat-new" title="New chat">\u2795 New</button>
                    </div>
                    <div class="pa-chat-dropdown-list" id="pa-chat-dropdown-list"></div>
                    <div class="pa-chat-dd-footer">
                        <button class="pa-btn pa-btn-sm pa-chat-action" id="pa-chat-activate" title="Switch Sapphire to this chat">\u{1F504} Activate</button>
                        <button class="pa-btn pa-btn-sm pa-chat-action" id="pa-chat-clear-msgs" title="Clear chat messages">\u{1F9F9} Clear</button>
                        <button class="pa-btn pa-btn-sm pa-chat-action pa-chat-danger" id="pa-chat-delete" title="Delete this chat">\u{1F5D1} Delete</button>
                    </div>
                </div>
            </div>
            <span class="pa-subtitle">Delegation chain — your team at work</span>
            <div class="pa-header-actions">
                <button class="pa-btn pa-toggle" id="pa-autocontinue-btn" title="Auto-Continue: when ON, lead persona automatically picks up delegate results and keeps going without waiting for you">
                    <span class="pa-toggle-dot" id="pa-autocontinue-dot"></span>
                    <span id="pa-autocontinue-label">Manual</span>
                </button>

                <button class="pa-btn" id="pa-log-btn" title="Toggle delegation log">\u{1F4CB} Log</button>
                <button class="pa-btn pa-btn-clear" id="pa-clear-btn" title="Clear transcript">\u{1F5D1} Clear</button>
            </div>
        </header>

        <div class="pa-context-row" id="pa-context-row">
            <div class="pa-ctx-selector">
                <span class="pa-ctx-icon">\u{1F464}</span>
                <button class="pa-ctx-btn" id="pa-lead-btn" title="Lead persona — who coordinates the team">
                    <span id="pa-lead-label">—</span> \u25BC
                </button>
                <div class="pa-ctx-dropdown" id="pa-lead-dropdown" style="display:none">
                    <div class="pa-ctx-dd-list" id="pa-lead-list"></div>
                </div>
            </div>
            <div class="pa-ctx-selector">
                <span class="pa-ctx-icon">\u{1F9F0}</span>
                <button class="pa-ctx-btn" id="pa-toolset-btn" title="Lead's toolset — what tools they can use">
                    <span id="pa-toolset-label">—</span> \u25BC
                </button>
                <div class="pa-ctx-dropdown" id="pa-toolset-dropdown" style="display:none">
                    <div class="pa-ctx-dd-list" id="pa-toolset-list"></div>
                </div>
            </div>
            <span class="pa-ctx-item" id="pa-ctx-model" title="Model"></span>
            <span class="pa-ctx-spacer"></span>
            <div class="pa-orb-wrap" id="pa-orb-wrap">
                <div class="pa-orb" id="pa-orb">
                    <div class="pa-orb-ring" id="pa-orb-ring"></div>
                    <button class="pa-orb-center" id="pa-orb-center" title="Nudge lead persona to continue"></button>
                </div>
                <span class="pa-orb-label" id="pa-orb-label">Nudge</span>
            </div>
            <span class="pa-ctx-spacer"></span>
            <div class="pa-ctx-bar">
                <div class="pa-ctx-track"><div class="pa-ctx-fill" id="pa-ctx-fill"></div></div>
                <span class="pa-ctx-label" id="pa-ctx-label">\u2014</span>
            </div>
        </div>

        <div class="pa-body">
            <div class="pa-roster-panel">
                <h3 class="pa-roster-title">\u{1F465} Agent Roster</h3>
                <div class="pa-roster-list" id="pa-roster-list">
                    <div class="pa-loading">Loading personas...</div>
                </div>
            </div>

            <div class="pa-transcript-panel">
                <div class="pa-transcript" id="pa-transcript">
                    <div class="pa-transcript-empty">\u{1F3AD} No delegations yet.<br>When your lead persona delegates tasks, each agent's work will appear here step by step.</div>
                </div>
                <div class="pa-chat-bar">
                    <textarea class="pa-chat-input" id="pa-chat-input" placeholder="Talk to your lead persona..." rows="1"></textarea>
                    <button class="pa-btn pa-btn-send" id="pa-chat-send" title="Send">\u{27A4}</button>
                    <button class="pa-btn pa-btn-tts-bar" id="pa-bar-tts" title="Play / stop last response TTS">\u{1F50A}</button>
                </div>
            </div>
        </div>

        <div class="pa-log-panel" id="pa-log-panel" style="display:none">
            <div class="pa-log-header">
                <h3>\u{1F4CB} Delegation Log</h3>
                <span class="pa-log-stats" id="pa-log-stats"></span>
                <button class="pa-btn pa-btn-sm" id="pa-log-refresh">\u{1F504} Refresh</button>
                <button class="pa-btn pa-btn-sm" id="pa-log-close">\u{2715}</button>
            </div>
            <pre class="pa-log-content" id="pa-log-content">Loading...</pre>
        </div>
    `;
}

// ─── Events ─────────────────────────────────────────────────────────────────

function _bindEvents(el) {
    el.querySelector('#pa-clear-btn').addEventListener('click', () => _clearTranscript());
    el.querySelector('#pa-autocontinue-btn').addEventListener('click', () => _toggleAutoContinue());
    el.querySelector('#pa-log-btn').addEventListener('click', () => _toggleLog());
    el.querySelector('#pa-log-refresh').addEventListener('click', () => _loadLog());
    el.querySelector('#pa-log-close').addEventListener('click', () => {
        document.getElementById('pa-log-panel').style.display = 'none';
    });

    // Chat selector dropdown
    el.querySelector('#pa-chat-toggle').addEventListener('click', () => _toggleChatDropdown());
    el.querySelector('#pa-chat-new').addEventListener('click', () => _chatNew());
    el.querySelector('#pa-chat-activate').addEventListener('click', () => _chatActivate());
    el.querySelector('#pa-chat-clear-msgs').addEventListener('click', () => _chatClearMsgs());
    el.querySelector('#pa-chat-delete').addEventListener('click', () => _chatDelete());
    document.addEventListener('click', e => {
        const dd = document.getElementById('pa-chat-dropdown');
        const btn = document.getElementById('pa-chat-toggle');
        if (dd && dd.style.display !== 'none' && !dd.contains(e.target) && e.target !== btn && !btn.contains(e.target)) {
            dd.style.display = 'none';
        }
    });

    // Lead persona & toolset dropdowns
    el.querySelector('#pa-lead-btn').addEventListener('click', () => _toggleLeadDropdown());
    el.querySelector('#pa-toolset-btn').addEventListener('click', () => _toggleToolsetDropdown());
    // Close context dropdowns on outside click
    document.addEventListener('click', e => {
        ['pa-lead-dropdown', 'pa-toolset-dropdown'].forEach(id => {
            const dd = document.getElementById(id);
            const btn = document.getElementById(id.replace('-dropdown', '-btn'));
            if (dd && dd.style.display !== 'none' && !dd.contains(e.target) && e.target !== btn && !btn?.contains(e.target)) {
                dd.style.display = 'none';
            }
        });
    });

    // Thinking orb nudge — both the orb and the label trigger it
    el.querySelector('#pa-orb-center').addEventListener('click', () => _orbNudge());
    el.querySelector('#pa-orb-label').addEventListener('click', () => _orbNudge());

    // Chat input — sends to the active chat (talks to lead persona)
    el.querySelector('#pa-chat-send').addEventListener('click', () => _sendChat());
    el.querySelector('#pa-chat-input').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _sendChat(); }
    });

    // TTS bar button — play/stop last assistant message
    el.querySelector('#pa-bar-tts').addEventListener('click', () => _toggleBarTts());
}

// ─── Data Loading ───────────────────────────────────────────────────────────

async function _loadPersonas() {
    try {
        const resp = await fetch(`${API}/personas`);
        const data = await resp.json();
        _allPersonas = data.personas || [];
        _renderRoster();
    } catch (e) {
        console.error('[PA] Failed to load personas:', e);
    }
}

// ─── Roster Panel ───────────────────────────────────────────────────────────

function _renderRoster() {
    const list = document.getElementById('pa-roster-list');
    if (!list) return;

    if (!_allPersonas.length) {
        list.innerHTML = '<div class="pa-roster-empty">No personas found</div>';
        return;
    }

    list.innerHTML = _allPersonas.map(p => {
        const color = p.trim_color || '#4a9eff';
        return `
            <div class="pa-roster-card" data-persona="${_esc(p.name)}" style="border-left-color: ${color}">
                <img class="pa-roster-avatar" src="/api/personas/${p.name}/avatar"
                     onerror="this.style.display='none'" style="border-color: ${color}">
                <div class="pa-roster-info">
                    <span class="pa-roster-name" style="color: ${color}">${_esc(p.display_name || p.name)}</span>
                    <span class="pa-roster-tagline">${_esc(p.tagline || '')}</span>
                    <span class="pa-roster-toolset">${_esc(p.toolset || 'none')} \u00B7 ${p.tool_count || 0} tools</span>
                </div>
                <span class="pa-roster-edit" title="Edit toolset">\u2699</span>
            </div>
        `;
    }).join('');

    // Bind click handlers on cards
    list.querySelectorAll('.pa-roster-card').forEach(card => {
        card.addEventListener('click', () => {
            const name = card.dataset.persona;
            const persona = _allPersonas.find(p => p.name === name);
            if (persona) _openToolsetEditor(persona);
        });
    });
}

// ─── Transcript ─────────────────────────────────────────────────────────────

function _startListening() {
    _viewVisible = true;

    // Subscribe to delegation events via SSE event bus
    _unsubscribers.push(
        // ── Core turn lifecycle ──
        eventBus.on('ai_typing_start', () => {
            _aiTyping = true;
            _startLiveTurn();
            _setOrbState('thinking');
        }),
        eventBus.on('ai_typing_end', () => {
            _aiTyping = false;
            _endLiveTurn();
            _fetchTranscript(true);
            setTimeout(() => _fetchTranscript(true), 1500);
        }),

        // ── Live streaming — capture actual text chunks ──
        eventBus.on('chat_chunk', (data) => {
            if (!_inLiveTurn || !data?.text) return;
            _onChatChunk(data.text);
        }),

        // ── Tool boundaries — segment splitting ──
        eventBus.on('tool_executing', (data) => {
            if (!_inLiveTurn || !data?.name) return;
            _onToolExecuting(data.name);
        }),
        eventBus.on('tool_complete', (data) => {
            if (!_inLiveTurn || !data?.name) return;
            _onToolComplete(data.name);
        }),

        // ── Delegation events ──
        eventBus.on('delegate_dispatched', (data) => {
            if (_selectedChat && data.chat_name !== _selectedChat) return;
            if (_inLiveTurn) {
                _liveSegments.push({
                    kind: 'dispatch',
                    timestamp: _localISOTimestamp(),
                    ...data,
                });
            }
            _fetchTranscript(true);
            _setOrbState('thinking');
        }),
        eventBus.on('delegate_completed', (data) => {
            if (_selectedChat && data.chat_name !== _selectedChat) return;
            _fetchTranscript(true);
            _showToast(data);
        }),

        // ── History commit ──
        eventBus.on('message_added', () => _fetchTranscript(true)),
    );
    // Initial fetch when view becomes visible
    _fetchTranscript(true);
    _updateContextBar();
}

function _stopListening() {
    _viewVisible = false;
    _aiTyping = false;
    _inLiveTurn = false;
    _liveSegments = [];
    _liveAccumulator = '';
    _unsubscribers.forEach(unsub => unsub());
    _unsubscribers = [];
}

let _fetchDebounce = null;
let _fetchInFlight = false;
let _fetchQueued = false;

function _fetchTranscript(immediate) {
    if (immediate) {
        if (_fetchDebounce) clearTimeout(_fetchDebounce);
        if (_fetchInFlight) {
            _fetchQueued = true;  // Queue a re-fetch after current completes
        } else {
            _doFetchTranscript();
        }
    } else {
        if (_fetchDebounce) clearTimeout(_fetchDebounce);
        _fetchDebounce = setTimeout(() => {
            if (_fetchInFlight) { _fetchQueued = true; }
            else { _doFetchTranscript(); }
        }, 250);
    }
}

async function _doFetchTranscript() {
    _fetchInFlight = true;
    try {
        const chatParam = _selectedChat ? `?chat_name=${encodeURIComponent(_selectedChat)}` : '';
        const [histResp, delResp] = await Promise.all([
            fetch('/api/history', { headers: { 'X-CSRF-Token': CSRF() } }),
            fetch(`${API}/session${chatParam}`),
        ]);

        const chatMessages = histResp.ok ? (await histResp.json()).messages || [] : [];
        const delData = delResp.ok ? await delResp.json() : {};
        const delTranscript = delData.transcript || [];
        const activeDelegates = delData.active_delegates || [];

        _renderTranscript(chatMessages, delTranscript, activeDelegates);
    } catch (e) {
        // Silent
    } finally {
        _fetchInFlight = false;
        // If events fired while we were fetching, do one more fetch
        if (_fetchQueued) {
            _fetchQueued = false;
            setTimeout(() => _doFetchTranscript(), 100);
        }
    }
}

function _renderTranscript(chatMessages, delTranscript, activeDelegates) {
    const container = document.getElementById('pa-transcript');
    if (!container) return;

    // During a live turn, preserve the live bubble — don't clobber it
    // unless the transcript data actually changed (delegation events, etc.)
    const isLive = _inLiveTurn;

    if (!chatMessages.length && !delTranscript.length && !activeDelegates.length && !_completedTurns.size) {
        container.innerHTML = '<div class="pa-transcript-empty">\u{1F3AD} No messages yet.<br>Send a message to your lead persona to get started.</div>';
        return;
    }

    // Identify which assistant messages we have live segments for.
    // If we tracked ANY delegation turns live this session, skip ALL history
    // messages that contain delegate_task tool calls — our segments replace them.
    const hasDelegationSegments = _completedTurns.size > 0 &&
        [..._completedTurns.values()].some(t => t.segments.some(s => s.kind === 'lead_summary'));

    const _isSegmentTracked = (msg) => {
        if (!hasDelegationSegments) return false;
        if (msg.role !== 'assistant' || !msg.parts) return false;
        return msg.parts.some(p => p.type === 'tool_call' && p.name === 'delegate_task');
    };

    // Merge chat messages and delegation events into a single timeline
    const timeline = [];

    for (const msg of chatMessages) {
        // Skip merged messages that we have live segments for
        if (_isSegmentTracked(msg)) {
            continue;
        }

        // For assistant messages with delegate_task calls, split into
        // pre-delegation and post-delegation segments so they sort
        // correctly around the delegate results in the timeline
        if (msg.role === 'assistant' && msg.parts) {
            const firstDelegateIdx = msg.parts.findIndex(p => p.type === 'tool_call' && p.name === 'delegate_task');
            if (firstDelegateIdx !== -1) {
                // Pre-delegation content (thinking + initial text before first delegate_task)
                const preParts = msg.parts.slice(0, firstDelegateIdx).filter(p => p.type === 'content' && p.text);
                if (preParts.length) {
                    const preText = preParts.map(p => {
                        const ex = _extractThink(p.text);
                        return ex.visible;
                    }).filter(Boolean).join('\n');
                    const preThink = preParts.map(p => _extractThink(p.text).thinking).filter(Boolean).join('\n');
                    if (preText || preThink) {
                        timeline.push({
                            sort_time: msg.timestamp || '',
                            kind: 'segment',
                            data: { kind: 'lead_text', text: preText, thinking: preThink, rawText: preParts.map(p => p.text).join('\n'), timestamp: msg.timestamp },
                        });
                    }
                }

                // Post-delegation content (summary after last delegate result)
                const lastDelegateResultIdx = msg.parts.map((p, i) => ({ p, i }))
                    .filter(x => x.p.type === 'tool_result' && x.p.name === 'delegate_task')
                    .pop()?.i ?? -1;
                if (lastDelegateResultIdx !== -1) {
                    const postParts = msg.parts.slice(lastDelegateResultIdx + 1).filter(p => p.type === 'content' && p.text);
                    if (postParts.length) {
                        const postText = postParts.map(p => _extractThink(p.text).visible).filter(Boolean).join('\n');
                        const sortTime = postParts[0]?.timestamp || msg.parts[lastDelegateResultIdx]?.timestamp || msg.timestamp || '';
                        if (postText) {
                            timeline.push({
                                sort_time: sortTime,
                                kind: 'segment',
                                data: { kind: 'lead_summary', text: postText, rawText: postParts.map(p => p.text).join('\n'), timestamp: sortTime },
                            });
                        }
                    }
                }
                continue;
            }
        }

        timeline.push({ sort_time: msg.timestamp || '', kind: 'chat', data: msg });
    }

    // Add delegation transcript entries
    for (const entry of delTranscript) {
        timeline.push({ sort_time: entry.timestamp || '', kind: entry.type, data: entry });
    }

    // Inject completed live segments into the timeline
    for (const [, turn] of _completedTurns) {
        for (const seg of turn.segments) {
            if (seg.kind === 'lead_text' || seg.kind === 'lead_summary') {
                if (seg.text) {
                    timeline.push({
                        sort_time: seg.timestamp || '',
                        kind: 'segment',
                        data: seg,
                    });
                }
            } else if (seg.kind === 'dispatch') {
                timeline.push({
                    sort_time: seg.timestamp || '',
                    kind: 'dispatch',
                    data: seg,
                });
            }
        }
    }

    // Sort chronologically by timestamp
    timeline.sort((a, b) => (a.sort_time || '').localeCompare(b.sort_time || ''));

    let html = '';
    for (const item of timeline) {
        if (item.kind === 'chat') {
            html += _renderChatMessage(item.data);
        } else if (item.kind === 'segment') {
            html += _renderSegment(item.data);
        } else if (item.kind === 'dispatch') {
            html += _renderDispatch(item.data);
        } else if (item.kind === 'result') {
            html += _renderResult(item.data);
        }
    }

    // Show active delegates with spinner
    for (const d of activeDelegates) {
        if (d.status === 'running') {
            html += _renderActive(d);
        }
    }

    container.innerHTML = html;

    // Re-attach live bubble if we're mid-turn
    if (isLive && _liveAccumulator) {
        _updateLiveBubble();
    }

    // Bind TTS buttons
    container.querySelectorAll('.pa-tts-btn').forEach(btn => {
        btn.addEventListener('click', () => _playTts(btn));
    });

    // Bind message action buttons
    container.querySelectorAll('.pa-regen-btn').forEach(btn => {
        btn.addEventListener('click', () => _regenFromButton(btn));
    });
    container.querySelectorAll('.pa-copy-btn').forEach(btn => {
        btn.addEventListener('click', () => _copyFromButton(btn));
    });
    container.querySelectorAll('.pa-delete-btn').forEach(btn => {
        btn.addEventListener('click', () => _deleteFromButton(btn));
    });
    container.querySelectorAll('.pa-tts-msg-btn').forEach(btn => {
        btn.addEventListener('click', () => _ttsMsgFromButton(btn));
    });

    // Update thinking orb state
    _updateOrbFromTranscript(delTranscript, activeDelegates, chatMessages);

    container.scrollTop = container.scrollHeight;
}

function _renderSegment(seg) {
    const leadName = _getLeadName();
    const color = _getLeadColor();
    const displayName = _getLeadDisplayName();
    const ts = seg.timestamp ? new Date(seg.timestamp).toLocaleTimeString() : '';

    const label = seg.kind === 'lead_summary' ? 'Summary' : '';
    const labelTag = label ? `<span class="pa-chat-tools">${label}</span>` : '';

    const thinkHtml = seg.thinking ? `
                <details class="pa-think-block">
                    <summary class="pa-think-summary">\u{1F4AD} Thinking</summary>
                    <div class="pa-think-content">${_esc(seg.thinking).replace(/\n/g, '<br>')}</div>
                </details>` : '';

    return `<div class="pa-chat-msg pa-chat-assistant">
        <div class="pa-chat-bubble pa-bubble-assistant" style="border-left-color:${color}">
            <div class="pa-chat-role">
                <img class="pa-chat-avatar" src="/api/personas/${_esc(leadName)}/avatar"
                     onerror="this.style.display='none'" style="border-color:${color}">
                <span style="color:${color}">${_esc(displayName)}</span>
                ${labelTag}
                ${ts ? `<span class="pa-chat-time">${ts}</span>` : ''}
                <span class="pa-msg-actions">
                    <button class="pa-action-btn pa-copy-btn" title="Copy">\u{1F4CB}</button>
                    <button class="pa-action-btn pa-tts-msg-btn" title="Play TTS">\u{1F50A}</button>
                </span>
            </div>
            ${thinkHtml}
            ${seg.text ? `<div class="pa-chat-text">${_esc(seg.text).replace(/\n/g, '<br>')}</div>` : ''}
        </div>
    </div>`;
}

function _renderChatMessage(msg) {
    const role = msg.role || '';
    if (role === 'system') return '';

    // Extract text + thinking content PER PART before joining
    // so an unclosed <|channel>thought in one part doesn't eat the next part
    let text = '';
    let thinkingText = '';
    if (msg.parts && Array.isArray(msg.parts)) {
        const extracted = msg.parts
            .filter(p => p.type === 'content' && p.text)
            .map(p => _extractThink(p.text));
        text = extracted.map(e => e.visible).filter(t => t).join('\n');
        thinkingText = extracted.map(e => e.thinking).filter(t => t).join('\n\n');
    }
    if (!text && msg.content) {
        const ex = _extractThink(typeof msg.content === 'string' ? msg.content : '');
        text = ex.visible;
        thinkingText = ex.thinking;
    }

    // Count tool calls for display
    const toolCalls = (msg.parts || []).filter(p => p.type === 'tool_call');
    const toolNames = toolCalls.map(t => t.name).filter(Boolean);

    // Skip messages with no text AND no tool calls (nothing to show)
    if (!text && !toolNames.length) return '';

    // Get persona info
    const persona = msg.persona || '';

    // Timestamp
    const ts = msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : '';
    const timeHtml = ts ? `<span class="pa-chat-time">${ts}</span>` : '';

    if (role === 'user') {
        // Store raw user text for regen lookups
        const safeText = text.replace(/'/g, '&#39;').replace(/"/g, '&quot;');
        return `<div class="pa-chat-msg pa-chat-user" data-user-text="${safeText}">
            <div class="pa-chat-bubble pa-bubble-user">
                <div class="pa-chat-role">
                    <img class="pa-chat-avatar" src="${_userAvatarUrl}"
                         onerror="this.src='/static/users/user.webp'">
                    <span>You</span>
                    ${timeHtml}
                    <span class="pa-msg-actions">
                        <button class="pa-action-btn pa-copy-btn" title="Copy">\u{1F4CB}</button>
                        <button class="pa-action-btn pa-delete-btn" title="Delete from here">\u{1F5D1}</button>
                    </span>
                </div>
                ${text ? `<div class="pa-chat-text">${_esc(text)}</div>` : ''}
            </div>
        </div>`;
    }

    if (role === 'assistant') {
        const model = msg.metadata?.model || '';
        const personaName = persona || 'Lead';
        const color = _getPersonaColor(persona);

        // Tool usage indicator
        let toolTag = '';
        if (toolNames.length) {
            const delegations = toolNames.filter(n => n === 'delegate_task').length;
            const results = toolNames.filter(n => n === 'get_delegate_result').length;
            const otherTools = toolNames.filter(n => n !== 'delegate_task' && n !== 'get_delegate_result');
            const parts = [];
            if (delegations) parts.push(`delegated ${delegations}`);
            if (results) parts.push(`retrieved ${results}`);
            if (otherTools.length) parts.push(otherTools.join(', '));
            toolTag = `<span class="pa-chat-tools">${parts.join(' \u00B7 ')}</span>`;
        }

        const modelTag = model ? `<span class="pa-chat-model">${_esc(model)}</span>` : '';

        const thinkHtml = thinkingText ? `
                <details class="pa-think-block">
                    <summary class="pa-think-summary">\u{1F4AD} Thinking</summary>
                    <div class="pa-think-content">${_esc(thinkingText).replace(/\n/g, '<br>')}</div>
                </details>` : '';

        return `<div class="pa-chat-msg pa-chat-assistant">
            <div class="pa-chat-bubble pa-bubble-assistant" style="border-left-color:${color}">
                <div class="pa-chat-role">
                    <img class="pa-chat-avatar" src="/api/personas/${_esc(persona)}/avatar"
                         onerror="this.style.display='none'" style="border-color:${color}">
                    <span style="color:${color}">${_esc(personaName)}</span>
                    ${modelTag}${toolTag}
                    ${timeHtml}
                    <span class="pa-msg-actions">
                        <button class="pa-action-btn pa-copy-btn" title="Copy">\u{1F4CB}</button>
                        <button class="pa-action-btn pa-tts-msg-btn" title="Play TTS">\u{1F50A}</button>
                        <button class="pa-action-btn pa-regen-btn" title="Regenerate">\u{1F504}</button>
                        <button class="pa-action-btn pa-delete-btn" title="Delete from here">\u{1F5D1}</button>
                    </span>
                </div>
                ${thinkHtml}
                ${text ? `<div class="pa-chat-text">${_esc(text).replace(/\n/g, '<br>')}</div>` : ''}
            </div>
        </div>`;
    }

    return '';
}

function _getPersonaColor(name) {
    if (!name) return '#4a9eff';
    const p = _allPersonas.find(p => p.name === name || p.display_name === name);
    return p?.trim_color || '#4a9eff';
}

async function _loadUserAvatar() {
    try {
        const resp = await fetch('/api/avatar/check/user');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.exists && data.path) {
            _userAvatarUrl = data.path;
        }
    } catch (e) {
        // Keep default
    }
}

function _stripThink(text) {
    // Remove <think>...</think> blocks and any raw thinking tags
    return text
        .replace(/<think>[\s\S]*?<\/think>\s*/gi, '')
        .replace(/<\|channel>thought[\s\S]*?(?=<\|channel>|$)/gi, '')
        .replace(/<think>\s*/gi, '')
        .replace(/<\/think>\s*/gi, '')
        .trim();
}

function _extractThink(text) {
    // Extract thinking content AND the remaining visible text
    const thoughts = [];
    // Capture <think>...</think> content
    text.replace(/<think>([\s\S]*?)<\/think>/gi, (_, inner) => {
        const t = inner.trim();
        if (t) thoughts.push(t);
        return '';
    });
    // Capture <|channel>thought content
    text.replace(/<\|channel>thought([\s\S]*?)(?=<\|channel>|$)/gi, (_, inner) => {
        const t = inner.trim();
        if (t) thoughts.push(t);
        return '';
    });
    const visible = _stripThink(text);
    return { thinking: thoughts.join('\n\n'), visible };
}

function _renderDispatch(entry) {
    const color = entry.trim_color || '#4a9eff';
    const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '';
    return `
        <div class="pa-system-msg">
            \u{1F4E8} <span style="color:${color};font-weight:700">${_esc(entry.display_name || entry.persona)}</span>
            dispatched
            ${entry.toolset ? `<span class="pa-tag">${_esc(entry.toolset)}</span>` : ''}
            <span class="pa-time">${time}</span>
            <div class="pa-task-preview">${_esc((entry.task || '').substring(0, 150))}</div>
        </div>
    `;
}

function _renderResult(entry) {
    const color = entry.trim_color || '#4a9eff';
    const statusIcon = entry.status === 'done' ? '\u2705' : '\u274C';
    const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '';
    const name = entry.display_name || entry.persona;
    const tools = (entry.tool_log || []).join(', ') || 'none';

    // Store voice settings in data attributes for TTS
    const voiceData = entry.voice ? `data-voice="${_esc(entry.voice)}"` : '';
    const pitchData = entry.pitch ? `data-pitch="${entry.pitch}"` : '';
    const speedData = entry.speed ? `data-speed="${entry.speed}"` : '';

    return `
        <div class="pa-message" style="border-left-color: ${color}">
            <div class="pa-msg-header">
                <img class="pa-msg-avatar" src="/api/personas/${entry.persona}/avatar"
                     onerror="this.style.display='none'" style="border-color:${color}">
                <span class="pa-msg-name" style="color:${color}">${_esc(name)}</span>
                <span class="pa-msg-meta">${statusIcon} ${entry.status} in ${entry.elapsed}s · tools: ${_esc(tools)}</span>
                <button class="pa-tts-btn" title="Play TTS" ${voiceData} ${pitchData} ${speedData}
                        data-persona="${_esc(entry.persona)}">\u{1F50A}</button>
                <span class="pa-time">${time}</span>
            </div>
            <div class="pa-msg-content">${_esc(_stripThink(entry.content || '(No result)')).replace(/\n/g, '<br>')}</div>
        </div>
    `;
}

function _renderActive(d) {
    const color = d.trim_color || '#4a9eff';
    const tools = (d.tool_log || []).join(', ') || 'working...';
    return `
        <div class="pa-message pa-message-active" style="border-left-color: ${color}">
            <div class="pa-msg-header">
                <img class="pa-msg-avatar" src="/api/personas/${d.persona}/avatar"
                     onerror="this.style.display='none'" style="border-color:${color}">
                <span class="pa-msg-name" style="color:${color}">${_esc(d.display_name || d.persona)}</span>
                <span class="pa-msg-meta">\u{1F7E1} working (${d.elapsed}s) · ${_esc(tools)}</span>
            </div>
            <div class="pa-typing">
                <span class="pa-typing-dots"><span>.</span><span>.</span><span>.</span></span>
            </div>
        </div>
    `;
}

// ─── TTS ────────────────────────────────────────────────────────────────────

async function _playTts(btn) {
    // If playing, stop
    if (_ttsPlaying) {
        await _stopTts();
        return;
    }

    const msg = btn.closest('.pa-message');
    const text = msg?.querySelector('.pa-msg-content')?.textContent || '';
    if (!text) return;

    btn.classList.add('pa-tts-loading');

    try {
        const resp = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ text: text.substring(0, 3000), output_mode: 'play' }),
        });
        if (!resp.ok) throw new Error(`TTS error: ${resp.status}`);

        btn.classList.remove('pa-tts-loading');
        btn.classList.add('pa-tts-playing');
        _ttsPlaying = true;

        const words = text.split(/\s+/).length;
        const durationMs = Math.max(2000, (words / 150) * 60000);
        setTimeout(() => {
            if (_ttsPlaying) {
                _ttsPlaying = false;
                btn.classList.remove('pa-tts-playing');
            }
        }, durationMs);
    } catch (e) {
        btn.classList.remove('pa-tts-loading');
        console.error('[PA] TTS failed:', e);
    }
}

// ─── Log Panel ──────────────────────────────────────────────────────────────

function _toggleLog() {
    const panel = document.getElementById('pa-log-panel');
    if (!panel) return;
    const showing = panel.style.display !== 'none';
    panel.style.display = showing ? 'none' : '';
    if (!showing) _loadLog();
}

async function _loadLog() {
    try {
        const [logResp, statsResp] = await Promise.all([
            fetch(`${API}/log?lines=200`),
            fetch(`${API}/log/stats`),
        ]);
        const logData = await logResp.json();
        const statsData = await statsResp.json();

        const content = document.getElementById('pa-log-content');
        if (content) {
            content.textContent = logData.log || '(no log entries yet)';
            content.scrollTop = content.scrollHeight;
        }

        const stats = document.getElementById('pa-log-stats');
        if (stats && statsData.exists) {
            stats.textContent = `${statsData.size_kb}KB`;
        } else if (stats) {
            stats.textContent = 'No log file yet';
        }
    } catch (e) {
        const content = document.getElementById('pa-log-content');
        if (content) content.textContent = `Error: ${e.message}`;
    }
}

// ─── Chat Selector ─────────────────────────────────────────────────────────

let _activeServerChat = '';  // The chat Sapphire is actually using

async function _loadChatSelector() {
    try {
        const resp = await fetch('/api/chats', { headers: { 'X-CSRF-Token': CSRF() } });
        const data = await resp.json();
        const chats = data.chats || [];
        _activeServerChat = data.active_chat || 'default';

        // Set current selection on first load
        if (!_selectedChat) _selectedChat = _activeServerChat;

        const label = document.getElementById('pa-chat-label');
        if (label) label.textContent = _selectedChat || _activeServerChat;

        // Populate dropdown list
        const list = document.getElementById('pa-chat-dropdown-list');
        if (list) {
            list.innerHTML = chats.map(c => {
                const name = c.name || c;
                const display = c.display_name || name;
                const isSelected = name === _selectedChat;
                const isServer = name === _activeServerChat;
                return `<div class="pa-chat-item ${isSelected ? 'pa-chat-item-active' : ''}" data-chat="${_esc(name)}">
                    <span class="pa-chat-item-name">${_esc(display)}</span>
                    ${isServer ? '<span class="pa-chat-item-badge">active</span>' : ''}
                </div>`;
            }).join('');

            list.querySelectorAll('.pa-chat-item').forEach(item => {
                item.addEventListener('click', async e => {
                    const chatName = item.dataset.chat;
                    _selectedChat = chatName;
                    const lbl = document.getElementById('pa-chat-label');
                    if (lbl) lbl.textContent = _selectedChat;
                    // Highlight selected
                    list.querySelectorAll('.pa-chat-item').forEach(i => i.classList.remove('pa-chat-item-active'));
                    item.classList.add('pa-chat-item-active');
                    // Auto-activate so main chat view shows it too
                    if (chatName !== _activeServerChat) {
                        try {
                            await fetch(`/api/chats/${encodeURIComponent(chatName)}/activate`, {
                                method: 'POST', headers: { 'X-CSRF-Token': CSRF() },
                            });
                            _activeServerChat = chatName;
                        } catch (err) { console.error('[PA] Auto-activate failed:', err); }
                    }
                    document.getElementById('pa-chat-dropdown').style.display = 'none';
                    _fetchTranscript();
                    _updateContextBar();
                });
            });
        }

        // Update footer button states
        const delBtn = document.getElementById('pa-chat-delete');
        const actBtn = document.getElementById('pa-chat-activate');
        if (delBtn) delBtn.disabled = _selectedChat === 'default';
        if (actBtn) actBtn.disabled = _selectedChat === _activeServerChat;
    } catch (e) {
        console.error('[PA] Failed to load chats:', e);
        const label = document.getElementById('pa-chat-label');
        if (label) label.textContent = 'default';
    }
}

function _toggleChatDropdown() {
    const dd = document.getElementById('pa-chat-dropdown');
    if (!dd) return;
    if (dd.style.display !== 'none') {
        dd.style.display = 'none';
    } else {
        dd.style.display = '';
        _loadChatSelector();
    }
}

// ─── Context Bar (Lead Persona + Toolset + Context Usage) ──────────────────

async function _updateContextBar() {
    try {
        const resp = await fetch('/api/status', { headers: { 'X-CSRF-Token': CSRF() } });
        if (!resp.ok) return;
        const data = await resp.json();

        const cs = data.chat_settings || {};
        const persona = cs.persona || '';
        const model = cs.llm_model || cs.llm_primary || '';

        // Update dropdown labels
        const toolsetObj = data.toolset || {};
        const toolsetName = toolsetObj.name || cs.toolset || '';
        const toolsetCount = toolsetObj.function_count || 0;

        const leadLabel = document.getElementById('pa-lead-label');
        const toolsetLabel = document.getElementById('pa-toolset-label');
        const modelEl = document.getElementById('pa-ctx-model');
        if (leadLabel) leadLabel.textContent = persona || 'none';
        if (toolsetLabel) toolsetLabel.textContent = toolsetName ? `${toolsetName} (${toolsetCount})` : 'none';
        if (modelEl) modelEl.textContent = model ? `\u{1F916} ${model}` : '';

        // Context usage bar
        const ctx = data.context || {};
        const pct = Math.min(ctx.percent || 0, 100);
        const used = ctx.used || 0;
        const limit = ctx.limit || 0;

        const fill = document.getElementById('pa-ctx-fill');
        const label = document.getElementById('pa-ctx-label');
        const row = document.getElementById('pa-context-row');

        if (fill) {
            fill.style.width = `${pct}%`;
            if (pct < 50) fill.style.background = 'linear-gradient(90deg, #00bcd4, #4caf50)';
            else if (pct < 70) fill.style.background = 'linear-gradient(90deg, #ffeb3b, #ff9800)';
            else if (pct < 90) fill.style.background = 'linear-gradient(90deg, #ff9800, #f44336)';
            else fill.style.background = 'linear-gradient(90deg, #f44336, #d32f2f)';
        }
        if (label) {
            const fmt = (n) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`;
            label.textContent = limit ? `${fmt(used)} / ${fmt(limit)} (${Math.round(pct)}%)` : `${Math.round(pct)}%`;
        }
        if (row) {
            row.classList.toggle('pa-ctx-critical', pct >= 90);
        }
    } catch (e) {
        // Silent
    }
}

// ── Lead Persona Dropdown ──────────────────────────────────────────────────

function _toggleLeadDropdown() {
    const dd = document.getElementById('pa-lead-dropdown');
    if (!dd) return;
    if (dd.style.display !== 'none') { dd.style.display = 'none'; return; }
    dd.style.display = '';
    _populateLeadDropdown();
}

async function _populateLeadDropdown() {
    const list = document.getElementById('pa-lead-list');
    if (!list) return;
    list.innerHTML = '<div class="pa-ctx-dd-loading">Loading...</div>';

    try {
        const resp = await fetch('/api/personas');
        const data = await resp.json();
        const personas = Object.entries(data.personas || data);
        const currentLabel = document.getElementById('pa-lead-label')?.textContent || '';

        list.innerHTML = personas.map(([name, p]) => {
            const display = p?.name || p?.display_name || name;
            const color = p?.settings?.trim_color || '#4a9eff';
            const isActive = name === currentLabel || display === currentLabel;
            return `<div class="pa-ctx-dd-item ${isActive ? 'pa-ctx-dd-active' : ''}" data-name="${_esc(name)}">
                <img class="pa-ctx-dd-avatar" src="/api/personas/${name}/avatar"
                     onerror="this.style.display='none'" style="border-color:${color}">
                <span style="color:${color}">${_esc(display)}</span>
            </div>`;
        }).join('');

        list.querySelectorAll('.pa-ctx-dd-item').forEach(item => {
            item.addEventListener('click', async () => {
                const name = item.dataset.name;
                try {
                    await fetch(`/api/personas/${encodeURIComponent(name)}/load`, {
                        method: 'POST',
                        headers: { 'X-CSRF-Token': CSRF() },
                    });
                    document.getElementById('pa-lead-dropdown').style.display = 'none';
                    _updateContextBar();
                } catch (e) {
                    console.error('[PA] Switch lead persona failed:', e);
                }
            });
        });
    } catch (e) {
        list.innerHTML = '<div class="pa-ctx-dd-loading">Failed to load</div>';
    }
}

// ── Toolset Dropdown ───────────────────────────────────────────────────────

function _toggleToolsetDropdown() {
    const dd = document.getElementById('pa-toolset-dropdown');
    if (!dd) return;
    if (dd.style.display !== 'none') { dd.style.display = 'none'; return; }
    dd.style.display = '';
    _populateToolsetDropdown();
}

async function _populateToolsetDropdown() {
    const list = document.getElementById('pa-toolset-list');
    if (!list) return;
    list.innerHTML = '<div class="pa-ctx-dd-loading">Loading...</div>';

    try {
        const resp = await fetch('/api/toolsets');
        const data = await resp.json();
        const toolsets = data.toolsets || [];
        const rawLabel = document.getElementById('pa-toolset-label')?.textContent || '';
        const currentToolset = rawLabel.replace(/\s*\(\d+\)$/, '');  // Strip " (42)" suffix

        list.innerHTML = toolsets.map(ts => {
            const name = ts.name || ts;
            const emoji = ts.emoji || '';
            const count = ts.function_count || 0;
            const isActive = name === currentToolset;
            return `<div class="pa-ctx-dd-item ${isActive ? 'pa-ctx-dd-active' : ''}" data-name="${_esc(name)}">
                <span class="pa-ctx-dd-emoji">${emoji || '\u{1F9F0}'}</span>
                <span class="pa-ctx-dd-name">${_esc(name)}</span>
                <span class="pa-ctx-dd-count">${count} tools</span>
            </div>`;
        }).join('');

        list.querySelectorAll('.pa-ctx-dd-item').forEach(item => {
            item.addEventListener('click', async () => {
                const name = item.dataset.name;
                try {
                    await fetch(`/api/toolsets/${encodeURIComponent(name)}/activate`, {
                        method: 'POST',
                        headers: { 'X-CSRF-Token': CSRF() },
                    });
                    document.getElementById('pa-toolset-dropdown').style.display = 'none';
                    _updateContextBar();
                } catch (e) {
                    console.error('[PA] Switch toolset failed:', e);
                }
            });
        });
    } catch (e) {
        list.innerHTML = '<div class="pa-ctx-dd-loading">Failed to load</div>';
    }
}

// ─── Chat Actions ──────────────────────────────────────────────────────────

async function _chatNew() {
    const name = prompt('New chat name:');
    if (!name || !name.trim()) return;
    try {
        await fetch('/api/chats', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ name: name.trim() }),
        });
        _selectedChat = name.trim();
        // Auto-activate the new chat
        await fetch(`/api/chats/${encodeURIComponent(_selectedChat)}/activate`, {
            method: 'POST', headers: { 'X-CSRF-Token': CSRF() },
        }).catch(() => {});
        _activeServerChat = _selectedChat;
        _loadChatSelector();
        _fetchTranscript();
        _updateContextBar();
    } catch (e) {
        console.error('[PA] New chat failed:', e);
    }
}

async function _chatActivate() {
    if (!_selectedChat || _selectedChat === _activeServerChat) return;
    try {
        await fetch(`/api/chats/${encodeURIComponent(_selectedChat)}/activate`, {
            method: 'POST',
            headers: { 'X-CSRF-Token': CSRF() },
        });
        _activeServerChat = _selectedChat;
        _loadChatSelector();
        _updateContextBar();
    } catch (e) {
        console.error('[PA] Activate chat failed:', e);
    }
}

async function _chatClearMsgs() {
    if (!_selectedChat) return;
    if (!confirm(`Clear all messages in "${_selectedChat}"?`)) return;
    try {
        // Clear the actual chat history (count: -1 = clear all)
        await fetch('/api/history/messages', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ count: -1 }),
        });
        // Also clear the delegation transcript for this chat
        await fetch(`${API}/session/clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ chat_name: _selectedChat }),
        });
        _completedTurns.clear();
        _fetchTranscript();
    } catch (e) {
        console.error('[PA] Clear chat failed:', e);
    }
}

async function _chatDelete() {
    if (!_selectedChat || _selectedChat === 'default') return;
    if (!confirm(`Delete chat "${_selectedChat}"? This cannot be undone.`)) return;
    try {
        await fetch(`/api/chats/${encodeURIComponent(_selectedChat)}`, {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': CSRF() },
        });
        // Also clear the delegation transcript
        await fetch(`${API}/session/clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ chat_name: _selectedChat }),
        });
        // Switch back to active chat
        _selectedChat = _activeServerChat;
        const label = document.getElementById('pa-chat-label');
        if (label) label.textContent = _selectedChat;
        _loadChatSelector();
        _fetchTranscript();
    } catch (e) {
        console.error('[PA] Delete chat failed:', e);
    }
}

// ─── Actions ────────────────────────────────────────────────────────────────

async function _clearTranscript() {
    try {
        await fetch(`${API}/session/clear`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ chat_name: _selectedChat }),
        });
        _fetchTranscript();  // Re-render — chat messages remain, delegation events cleared
    } catch (e) {
        console.error('[PA] Clear failed:', e);
    }
}

// ─── Chat Input ─────────────────────────────────────────────────────────────

// ─── Message Action Handlers ───────────────────────────────────────────────

function _copyFromButton(btn) {
    const bubble = btn.closest('.pa-chat-bubble');
    if (!bubble) return;
    const textEl = bubble.querySelector('.pa-chat-text');
    const text = textEl ? textEl.textContent : '';
    if (!text) return;
    navigator.clipboard.writeText(text).then(() => {
        btn.textContent = '\u2705';
        setTimeout(() => { btn.textContent = '\u{1F4CB}'; }, 1500);
    });
}

async function _deleteFromButton(btn) {
    const msgEl = btn.closest('.pa-chat-msg');
    if (!msgEl) return;

    // Find the user message text — either this IS a user msg, or find the one before
    let userText = msgEl.dataset.userText;
    if (!userText) {
        // Assistant message — find preceding user message
        let prev = msgEl.previousElementSibling;
        while (prev && !prev.dataset.userText) {
            prev = prev.previousElementSibling;
        }
        userText = prev?.dataset.userText;
    }
    if (!userText) return;

    userText = userText.replace(/&#39;/g, "'").replace(/&quot;/g, '"');

    try {
        await fetch('/api/history/messages', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ user_message: userText }),
        });
        _completedTurns.clear();
        _fetchTranscript(true);
    } catch (e) {
        console.error('[PA] Delete failed:', e);
    }
}

async function _ttsMsgFromButton(btn) {
    // If TTS is playing (tracked via SSE events), stop it
    if (_ttsPlaying) {
        await _stopTts();
        return;
    }
    const bubble = btn.closest('.pa-chat-bubble');
    if (!bubble) return;
    const textEl = bubble.querySelector('.pa-chat-text');
    const text = textEl ? textEl.textContent : '';
    if (!text) return;
    _playTtsText(text, btn);
}

async function _playTtsText(text, btn) {
    if (!text) return;
    if (btn) btn.classList.add('pa-tts-loading');
    try {
        const resp = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ text, output_mode: 'play' }),
        });
        if (!resp.ok) throw new Error('TTS failed');
        if (btn) {
            btn.classList.remove('pa-tts-loading');
            btn.classList.add('pa-tts-playing');
        }
        _ttsPlaying = true;
        const words = text.split(/\s+/).length;
        const durationMs = Math.max(2000, (words / 150) * 60000);
        setTimeout(() => {
            if (_ttsPlaying) {
                _ttsPlaying = false;
                if (btn) btn.classList.remove('pa-tts-playing');
            }
        }, durationMs);
    } catch (e) {
        if (btn) {
            btn.classList.remove('pa-tts-loading');
            btn.classList.remove('pa-tts-playing');
        }
        console.error('[PA] TTS failed:', e);
    }
}

async function _regenFromButton(btn) {
    // Walk up to find the assistant bubble, then look backwards for the preceding user message
    const assistantMsg = btn.closest('.pa-chat-msg');
    if (!assistantMsg) return;

    // Find the previous user message in the DOM
    let userMsg = assistantMsg.previousElementSibling;
    while (userMsg && !userMsg.dataset.userText) {
        userMsg = userMsg.previousElementSibling;
    }
    if (!userMsg?.dataset.userText) {
        console.warn('[PA] No user message found to regenerate from');
        return;
    }

    const userText = userMsg.dataset.userText
        .replace(/&#39;/g, "'").replace(/&quot;/g, '"');

    // Delete from that user message onwards
    try {
        await fetch('/api/history/messages', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ user_message: userText }),
        });

        // Clear live state so we get a fresh turn
        _completedTurns.clear();
        _liveSegments = [];
        _liveAccumulator = '';

        // Re-stream the same message
        const { triggerSendWithText } = await import('/static/handlers/send-handlers.js');
        await triggerSendWithText(userText);
    } catch (e) {
        console.error('[PA] Regenerate failed:', e);
    }
}

async function _sendChat() {
    const input = document.getElementById('pa-chat-input');
    if (!input) return;
    const text = input.value.trim();
    if (!text) return;

    input.value = '';
    _aiTyping = true;
    _setOrbState('thinking');

    try {
        const { triggerSendWithText } = await import('/static/handlers/send-handlers.js');
        await triggerSendWithText(text);
    } catch (e) {
        _aiTyping = false;
        console.error('[PA] Send failed:', e);
        // Restore text on failure
        input.value = text;
    }
}

// ─── Live Streaming — Segment Tracking ─────────────────────────────────────
// Tracks assistant turns via SSE events, splitting them into segments with
// accurate timestamps. This replaces the merged-history rendering for live turns.

function _localISOTimestamp() {
    // Match server timestamp format: local time without Z suffix
    const d = new Date();
    const pad = (n, len = 2) => String(n).padStart(len, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}000`;
}

function _startLiveTurn() {
    _inLiveTurn = true;
    _liveTurnId++;
    _liveSegments = [];
    _liveAccumulator = '';
    _liveSegmentStart = _localISOTimestamp();
    _livePreDelegation = true;
}

function _onChatChunk(text) {
    _liveAccumulator += text;
    _updateLiveBubble();
}

function _onToolExecuting(toolName) {
    if (toolName === 'delegate_task') {
        // Finalize pre-delegation text as its own segment
        if (_liveAccumulator.trim()) {
            const stripped = _stripThink(_liveAccumulator);
            _liveSegments.push({
                kind: 'lead_text',
                text: stripped,
                rawText: _liveAccumulator,
                timestamp: _liveSegmentStart,
                hasDelegation: true,
            });
        }
        _liveAccumulator = '';
        _livePreDelegation = false;
    }
    // For other tools, just note them (don't split segments)
}

function _onToolComplete(toolName) {
    if (toolName === 'delegate_task' || toolName === 'get_delegate_result') {
        // After delegation tool completes, start a new text segment
        // The next chat_chunk content will be the summary
        _liveAccumulator = '';
        _liveSegmentStart = _localISOTimestamp();
    }
}

function _endLiveTurn() {
    _inLiveTurn = false;

    // Finalize any remaining accumulated text as the final segment
    if (_liveAccumulator.trim()) {
        const stripped = _stripThink(_liveAccumulator);
        if (stripped) {
            _liveSegments.push({
                kind: 'lead_summary',
                text: stripped,
                rawText: _liveAccumulator,
                timestamp: _liveSegmentStart,
            });
        }
    }
    _liveAccumulator = '';

    // Store completed turn with its segments
    if (_liveSegments.length > 0) {
        const turnKey = `turn_${_liveTurnId}`;
        _completedTurns.set(turnKey, {
            segments: [..._liveSegments],
            startTimestamp: _liveSegments[0]?.timestamp || '',
        });
    }
    _liveSegments = [];

    // Remove live bubble
    const liveBubble = document.getElementById('pa-live-bubble');
    if (liveBubble) liveBubble.remove();
}

function _updateLiveBubble() {
    const container = document.getElementById('pa-transcript');
    if (!container) return;

    let bubble = document.getElementById('pa-live-bubble');
    if (!bubble) {
        bubble = document.createElement('div');
        bubble.id = 'pa-live-bubble';
        bubble.className = 'pa-chat-msg pa-chat-assistant';
        container.appendChild(bubble);
    }

    // Extract thinking + visible text for live display
    const { thinking, visible } = _extractThink(_liveAccumulator);
    const leadColor = _getLeadColor();
    const leadName = _getLeadName();
    const leadDisplay = _getLeadDisplayName();

    // Build HTML for any already-saved segments (pre-delegation text)
    let priorSegmentsHtml = '';
    for (const seg of _liveSegments) {
        if (seg.kind === 'lead_text' && seg.rawText) {
            const { thinking: segThink, visible: segVis } = _extractThink(seg.rawText);
            const segThinkHtml = segThink ? `
                <details class="pa-think-block" open>
                    <summary>\u{1F4AD} Thinking</summary>
                    <div class="pa-think-text">${_esc(segThink).replace(/\n/g, '<br>')}</div>
                </details>` : '';
            const segVisHtml = segVis ? `<div class="pa-chat-text">${_esc(segVis).replace(/\n/g, '<br>')}</div>` : '';
            priorSegmentsHtml += segThinkHtml + segVisHtml;
        }
    }

    const headerHtml = `
        <div class="pa-chat-role">
            <img class="pa-chat-avatar" src="/api/personas/${leadName}/avatar"
                 onerror="this.style.display='none'" style="border-color:${leadColor}">
            <span style="color:${leadColor}">${_esc(leadDisplay)}</span>
            <span class="pa-chat-time">${new Date().toLocaleTimeString()}</span>
        </div>`;

    // Show live thinking if we have it
    const liveThinkHtml = thinking ? `
        <div class="pa-think-live">
            <span class="pa-think-live-label">\u{1F4AD} Thinking</span>
            <div class="pa-think-live-text">${_esc(thinking).replace(/\n/g, '<br>')}</div>
        </div>` : '';

    if (!visible && !thinking && !priorSegmentsHtml) {
        // Nothing yet — show typing dots
        bubble.innerHTML = `
            <div class="pa-chat-bubble pa-bubble-assistant" style="border-left-color:${leadColor}">
                ${headerHtml}
                <div class="pa-typing"><span class="pa-typing-dots"><span>.</span><span>.</span><span>.</span></span></div>
            </div>`;
    } else if (!visible && !thinking && priorSegmentsHtml) {
        // Pre-delegation content saved, now waiting for delegate — show prior + dots
        bubble.innerHTML = `
            <div class="pa-chat-bubble pa-bubble-assistant" style="border-left-color:${leadColor}">
                ${headerHtml}
                ${priorSegmentsHtml}
                <div class="pa-typing"><span class="pa-typing-dots"><span>.</span><span>.</span><span>.</span></span> Delegating...</div>
            </div>`;
    } else if (!visible && thinking) {
        // Still in thinking — show live thoughts
        bubble.innerHTML = `
            <div class="pa-chat-bubble pa-bubble-assistant" style="border-left-color:${leadColor}">
                ${headerHtml}
                ${priorSegmentsHtml}
                ${liveThinkHtml}
                <div class="pa-typing"><span class="pa-typing-dots"><span>.</span><span>.</span><span>.</span></span></div>
            </div>`;
    } else {
        // Have visible text (and maybe thinking)
        bubble.innerHTML = `
            <div class="pa-chat-bubble pa-bubble-assistant" style="border-left-color:${leadColor}">
                ${headerHtml}
                ${priorSegmentsHtml}
                ${liveThinkHtml}
                <div class="pa-chat-text">${_esc(visible).replace(/\n/g, '<br>')}</div>
            </div>`;
    }

    container.scrollTop = container.scrollHeight;
}

function _getLeadName() {
    const label = document.getElementById('pa-lead-label');
    return label?.textContent?.trim() || 'lead';
}

function _getLeadDisplayName() {
    const name = _getLeadName();
    const p = _allPersonas.find(p => p.name === name);
    return p?.display_name || name;
}

function _getLeadColor() {
    return _getPersonaColor(_getLeadName());
}

// ─── Thinking Orb ──────────────────────────────────────────────────────────

let _orbState = 'idle'; // 'idle' | 'thinking' | 'nudge'
let _aiTyping = false;  // true while lead persona is actively responding

function _setOrbState(state) {
    _orbState = state;
    const wrap = document.getElementById('pa-orb-wrap');
    const center = document.getElementById('pa-orb-center');
    if (!wrap || !center) return;

    wrap.classList.remove('pa-orb-idle', 'pa-orb-thinking', 'pa-orb-nudge');
    wrap.classList.add(`pa-orb-${state}`);
    center.disabled = state !== 'nudge';
}

function _updateOrbFromTranscript(delTranscript, activeDelegates, chatMessages) {
    const stillRunning = activeDelegates.some(d => d.status === 'running');

    if (stillRunning || _aiTyping) {
        // Delegates working or lead actively responding — spin
        _setOrbState('thinking');
    } else if (_hasUnretrievedResults(delTranscript, chatMessages)) {
        // Fallback: results exist but lead didn't summarize (e.g. timeout)
        _setOrbState('nudge');
    } else {
        _setOrbState('idle');
    }
}

function _hasUnretrievedResults(delTranscript, chatMessages) {
    // Check if there are delegate results that lead hasn't addressed yet
    // With synchronous delegation this should rarely happen — only on timeout/error
    const hasResults = delTranscript.some(e => e.type === 'result');
    if (!hasResults) return false;

    // If lead's last message contains a summary (text content after delegation), they handled it
    const lastAssistant = [...chatMessages].reverse().find(m => m.role === 'assistant');
    if (!lastAssistant) return true;

    // Check if her last message had actual text (not just tool calls)
    const hasText = (lastAssistant.parts || []).some(p => p.type === 'content' && p.text?.trim());
    const hasDelegateCall = (lastAssistant.parts || []).some(
        p => p.type === 'tool_call' && p.name === 'delegate_task'
    );

    // If her last message was the delegation call with no text after, results may be unretrieved
    return hasDelegateCall && !hasText;
}

async function _orbNudge() {
    if (_orbState !== 'nudge') return;
    _aiTyping = true;
    _setOrbState('thinking');
    try {
        const { triggerSendWithText } = await import('/static/handlers/send-handlers.js');
        await triggerSendWithText(
            'All delegates have reported back. Please retrieve their results with get_delegate_result and give me a complete summary.'
        );
    } catch (e) {
        console.error('[PA] Orb nudge failed:', e);
        _setOrbState('nudge');
    }
}

// ─── Bar TTS (Play/Stop last assistant message) ────────────────────────────

// _barTtsPlayer removed — replaced by _ttsPlaying + server-side playback

let _ttsPlaying = false;  // Track server-side TTS state

async function _stopTts() {
    // Stop browser-side audio (main chat's Audio element) + server-side playback
    audio.stop(true);
    _ttsPlaying = false;
    _updateBarTtsState();
    // Clear all TTS button states
    document.querySelectorAll('.pa-tts-playing, .pa-tts-loading').forEach(el => {
        el.classList.remove('pa-tts-playing', 'pa-tts-loading');
    });
}

function _updateBarTtsState() {
    const btn = document.getElementById('pa-bar-tts');
    if (!btn) return;
    if (_ttsPlaying) {
        btn.textContent = '\u23F9';  // ⏹ stop icon
        btn.title = 'Stop TTS';
        btn.classList.add('pa-tts-playing');
    } else {
        btn.textContent = '\u{1F50A}';  // 🔊 speaker icon
        btn.title = 'Play / stop last response TTS';
        btn.classList.remove('pa-tts-playing');
    }
}

async function _toggleBarTts() {
    const btn = document.getElementById('pa-bar-tts');
    if (!btn) return;
    // If we know TTS is playing (via SSE events), stop it immediately
    if (_ttsPlaying) {
        await _stopTts();
        return;
    }

    // Double-check server status as safety net (covers edge cases)
    try {
        const statusResp = await fetch('/api/tts/status', { headers: { 'X-CSRF-Token': CSRF() } });
        const statusData = await statusResp.json().catch(() => ({}));
        if (statusData.playing) {
            await _stopTts();
            return;
        }
    } catch (e) { /* proceed to play */ }

    // Find last assistant message in transcript
    const container = document.getElementById('pa-transcript');
    if (!container) return;

    const bubbles = container.querySelectorAll('.pa-bubble-assistant .pa-chat-text');
    const lastBubble = bubbles.length ? bubbles[bubbles.length - 1] : null;
    const results = container.querySelectorAll('.pa-msg-content');
    const lastResult = results.length ? results[results.length - 1] : null;

    let textEl = null;
    if (lastBubble && lastResult) {
        const pos = lastBubble.compareDocumentPosition(lastResult);
        textEl = (pos & Node.DOCUMENT_POSITION_FOLLOWING) ? lastResult : lastBubble;
    } else {
        textEl = lastBubble || lastResult;
    }

    const text = textEl?.textContent?.trim();
    if (!text) return;

    btn.classList.add('pa-tts-loading');

    try {
        const resp = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ text: text.substring(0, 3000), output_mode: 'play' }),
        });
        if (!resp.ok) throw new Error(`TTS error: ${resp.status}`);

        btn.classList.remove('pa-tts-loading');
        btn.classList.add('pa-tts-playing');
        _ttsPlaying = true;

        // Estimate duration and auto-clear (server-side playback has no end event)
        const words = text.split(/\s+/).length;
        const durationMs = Math.max(2000, (words / 150) * 60000);
        setTimeout(() => {
            if (_ttsPlaying) {
                _ttsPlaying = false;
                btn.classList.remove('pa-tts-playing');
            }
        }, durationMs);
    } catch (e) {
        btn.classList.remove('pa-tts-loading');
        console.error('[PA] Bar TTS failed:', e);
    }
}

// ─── Toast Notifications ────────────────────────────────────────────────────

function _setupToastNotifications() {
    // Global listener — toasts show even when not on the Agents view
    eventBus.on('delegate_completed', (data) => {
        if (_viewVisible) return;  // Skip toast when viewing the Agents panel (they see it live)
        _showToast(data);
    });
}

function _showToast(data) {
    if (!data?.display_name && !data?.persona) return;
    const name = data.display_name || data.persona;
    const icon = data.status === 'done' ? '\u2705' : '\u274C';
    const msg = `${icon} ${name} finished (${data.elapsed || '?'}s)`;
    const container = document.getElementById('toast-container');
    if (container) {
        const toast = document.createElement('div');
        toast.className = 'toast info';
        toast.innerHTML = `<span class="toast-text">${msg}</span>`;
        container.appendChild(toast);
        setTimeout(() => toast.remove(), 5000);
    }
}

// ─── Auto-Continue ─────────────────────────────────────────────────────────

function _toggleAutoContinue() {
    _autoContinue = !_autoContinue;
    _updateAutoContinueUI();
    // Persist to plugin settings so backend/scheduled tasks can read it
    fetch(`${API}/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
        body: JSON.stringify({ auto_continue: _autoContinue }),
    }).catch(() => {});
}

function _updateAutoContinueUI() {
    const dot = document.getElementById('pa-autocontinue-dot');
    const label = document.getElementById('pa-autocontinue-label');
    const btn = document.getElementById('pa-autocontinue-btn');
    if (dot) dot.classList.toggle('pa-toggle-on', _autoContinue);
    if (label) label.textContent = _autoContinue ? 'Auto' : 'Manual';
    if (btn) btn.classList.toggle('pa-toggle-active', _autoContinue);
}

async function _loadAutoContinueSetting() {
    try {
        const resp = await fetch(`${API}/settings`);
        if (!resp.ok) return;
        const data = await resp.json();
        _autoContinue = data.auto_continue === true;
        _updateAutoContinueUI();
    } catch (e) {
        // Default to manual
    }
}

// ─── Toolset Editor Modal ──────────────────────────────────────────────────
// Clicking a roster card opens this modal. User can:
//   1. Switch which toolset a persona uses (dropdown)
//   2. Add/remove individual tools (checkboxes grouped by module)
//   3. Save changes to the current toolset (updates it for ALL personas using it)
//   4. "Save As" to create a new toolset (and assign it to this persona)

let _editorPersona = null;          // Persona being edited
let _editorToolsets = [];            // All available toolsets
let _editorFunctions = {};           // All functions grouped by module
let _editorSelectedToolset = '';     // Currently selected toolset name
let _editorCheckedFns = new Set();   // Currently checked function names
let _editorOriginalFns = new Set();  // Original functions (to detect changes)

function _openToolsetEditor(persona) {
    _editorPersona = persona;
    _editorSelectedToolset = persona.toolset || 'conversation';
    // Fetch toolsets + functions in parallel, then render
    Promise.all([
        fetch('/api/toolsets').then(r => r.json()),
        fetch('/api/functions').then(r => r.json()),
    ]).then(([tsData, fnData]) => {
        _editorToolsets = tsData.toolsets || [];
        _editorFunctions = fnData.modules || {};
        // Set initial checked state from persona's current toolset
        const ts = _editorToolsets.find(t => t.name === _editorSelectedToolset);
        _editorCheckedFns = new Set(ts ? ts.functions : []);
        _editorOriginalFns = new Set(_editorCheckedFns);
        _renderEditorModal();
    }).catch(err => {
        console.error('[PA] Failed to load toolset editor data:', err);
    });
}

function _renderEditorModal() {
    // Remove existing modal if any
    document.getElementById('pa-editor-overlay')?.remove();

    const p = _editorPersona;
    const color = p.trim_color || '#4a9eff';

    // Build toolset dropdown options
    const tsOpts = _editorToolsets
        .filter(t => t.name !== '_comment')
        .map(t => {
            const sel = t.name === _editorSelectedToolset ? 'selected' : '';
            const emoji = t.emoji ? `${t.emoji} ` : '';
            return `<option value="${_esc(t.name)}" ${sel}>${emoji}${_esc(t.name)} (${t.function_count} tools)</option>`;
        }).join('');

    // Build function groups
    const groupsHtml = _buildFunctionGroups();

    // Count checked
    const checkedCount = _editorCheckedFns.size;
    const hasChanges = !_setsEqual(_editorCheckedFns, _editorOriginalFns);

    const overlay = document.createElement('div');
    overlay.id = 'pa-editor-overlay';
    overlay.innerHTML = `
        <div class="pa-editor-modal">
            <div class="pa-editor-header" style="border-bottom-color: ${color}">
                <img class="pa-editor-avatar" src="/api/personas/${p.name}/avatar"
                     onerror="this.style.display='none'" style="border-color: ${color}">
                <div class="pa-editor-persona-info">
                    <span class="pa-editor-name" style="color: ${color}">${_esc(p.display_name || p.name)}</span>
                    <span class="pa-editor-tagline">${_esc(p.tagline || '')}</span>
                </div>
                <button class="pa-editor-close" id="pa-editor-close">\u2715</button>
            </div>

            <div class="pa-editor-toolset-row">
                <label class="pa-editor-label">Toolset</label>
                <select class="pa-editor-select" id="pa-editor-toolset-select">${tsOpts}</select>
                <span class="pa-editor-tool-count" id="pa-editor-tool-count">${checkedCount} tools active</span>
            </div>

            <div class="pa-editor-search-row">
                <input type="text" class="pa-editor-search" id="pa-editor-search"
                       placeholder="Search tools..." autocomplete="off">
            </div>

            <div class="pa-editor-body" id="pa-editor-body">
                ${groupsHtml}
            </div>

            <div class="pa-editor-footer">
                <div class="pa-editor-footer-left">
                    <span class="pa-editor-dirty ${hasChanges ? 'pa-visible' : ''}" id="pa-editor-dirty">\u26A0 Unsaved changes</span>
                </div>
                <div class="pa-editor-footer-right">
                    <button class="pa-btn pa-editor-btn" id="pa-editor-cancel">Cancel</button>
                    <button class="pa-btn pa-editor-btn pa-editor-btn-secondary" id="pa-editor-save-as">Save As New\u2026</button>
                    <button class="pa-btn pa-editor-btn pa-editor-btn-primary" id="pa-editor-save">Save</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    // Bind modal events
    overlay.querySelector('#pa-editor-close').addEventListener('click', () => _closeEditor());
    overlay.querySelector('#pa-editor-cancel').addEventListener('click', () => _closeEditor());
    overlay.addEventListener('click', e => { if (e.target === overlay) _closeEditor(); });

    overlay.querySelector('#pa-editor-toolset-select').addEventListener('change', e => {
        _editorSelectToolset(e.target.value);
    });

    overlay.querySelector('#pa-editor-search').addEventListener('input', e => {
        _editorFilterTools(e.target.value);
    });

    overlay.querySelector('#pa-editor-save').addEventListener('click', () => _editorSave());
    overlay.querySelector('#pa-editor-save-as').addEventListener('click', () => _editorSaveAs());

    // Checkbox delegation
    overlay.querySelector('#pa-editor-body').addEventListener('change', e => {
        if (e.target.classList.contains('pa-fn-check')) {
            const fn = e.target.dataset.fn;
            if (e.target.checked) _editorCheckedFns.add(fn);
            else _editorCheckedFns.delete(fn);
            _updateEditorState();
        }
    });

    // Group toggle (select all / deselect all in a module)
    overlay.querySelector('#pa-editor-body').addEventListener('click', e => {
        if (e.target.classList.contains('pa-group-toggle')) {
            const group = e.target.closest('.pa-fn-group');
            if (!group) return;
            const checks = group.querySelectorAll('.pa-fn-check');
            const allChecked = [...checks].every(c => c.checked);
            checks.forEach(c => {
                c.checked = !allChecked;
                if (c.checked) _editorCheckedFns.add(c.dataset.fn);
                else _editorCheckedFns.delete(c.dataset.fn);
            });
            _updateEditorState();
        }
    });
}

function _buildFunctionGroups() {
    const modules = _editorFunctions;
    let html = '';

    for (const [modName, mod] of Object.entries(modules)) {
        const fns = mod.functions || [];
        if (!fns.length) continue;

        const emoji = mod.emoji || '\u{1F527}';
        const activeInGroup = fns.filter(f => _editorCheckedFns.has(f.name)).length;

        html += `<div class="pa-fn-group" data-module="${_esc(modName)}">`;
        html += `<div class="pa-fn-group-header">`;
        html += `<span class="pa-fn-group-icon">${emoji}</span>`;
        html += `<span class="pa-fn-group-name">${_esc(modName)}</span>`;
        html += `<span class="pa-fn-group-count">${activeInGroup}/${fns.length}</span>`;
        html += `<button class="pa-group-toggle" title="Toggle all">\u21C5</button>`;
        html += `</div>`;

        html += `<div class="pa-fn-list">`;
        for (const fn of fns) {
            const checked = _editorCheckedFns.has(fn.name) ? 'checked' : '';
            const net = fn.is_network ? '<span class="pa-fn-net" title="Network tool">\u{1F310}</span>' : '';
            html += `
                <label class="pa-fn-item" data-fn-name="${_esc(fn.name)}">
                    <input type="checkbox" class="pa-fn-check" data-fn="${_esc(fn.name)}" ${checked}>
                    <span class="pa-fn-name">${_esc(fn.name)}</span>
                    ${net}
                    <span class="pa-fn-desc">${_esc(fn.description || '')}</span>
                </label>`;
        }
        html += `</div></div>`;
    }

    return html;
}

function _editorSelectToolset(name) {
    _editorSelectedToolset = name;
    const ts = _editorToolsets.find(t => t.name === name);
    _editorCheckedFns = new Set(ts ? ts.functions : []);
    _editorOriginalFns = new Set(_editorCheckedFns);

    // Re-render checkboxes
    const body = document.getElementById('pa-editor-body');
    if (body) body.innerHTML = _buildFunctionGroups();
    _updateEditorState();
}

function _editorFilterTools(query) {
    const q = query.toLowerCase().trim();
    const items = document.querySelectorAll('#pa-editor-body .pa-fn-item');
    const groups = document.querySelectorAll('#pa-editor-body .pa-fn-group');

    items.forEach(item => {
        const name = (item.dataset.fnName || '').toLowerCase();
        const desc = (item.querySelector('.pa-fn-desc')?.textContent || '').toLowerCase();
        item.style.display = (!q || name.includes(q) || desc.includes(q)) ? '' : 'none';
    });

    // Hide groups with no visible items
    groups.forEach(group => {
        const visible = group.querySelectorAll('.pa-fn-item[style=""], .pa-fn-item:not([style])');
        // Check if any item is not display:none
        const hasVisible = [...group.querySelectorAll('.pa-fn-item')].some(i => i.style.display !== 'none');
        group.style.display = hasVisible ? '' : 'none';
    });
}

function _updateEditorState() {
    const countEl = document.getElementById('pa-editor-tool-count');
    if (countEl) countEl.textContent = `${_editorCheckedFns.size} tools active`;

    const dirty = document.getElementById('pa-editor-dirty');
    const hasChanges = !_setsEqual(_editorCheckedFns, _editorOriginalFns) ||
                       _editorSelectedToolset !== (_editorPersona?.toolset || 'conversation');
    if (dirty) dirty.classList.toggle('pa-visible', hasChanges);

    // Update group counts
    document.querySelectorAll('#pa-editor-body .pa-fn-group').forEach(group => {
        const checks = group.querySelectorAll('.pa-fn-check');
        const active = [...checks].filter(c => c.checked).length;
        const countSpan = group.querySelector('.pa-fn-group-count');
        if (countSpan) countSpan.textContent = `${active}/${checks.length}`;
    });
}

function _setsEqual(a, b) {
    if (a.size !== b.size) return false;
    for (const x of a) if (!b.has(x)) return false;
    return true;
}

async function _editorSave() {
    const fns = [..._editorCheckedFns];
    const toolsetName = _editorSelectedToolset;
    const personaName = _editorPersona?.name;
    if (!personaName) return;

    try {
        // If the checked fns differ from the toolset's original, update the toolset
        if (!_setsEqual(_editorCheckedFns, _editorOriginalFns)) {
            await fetch('/api/toolsets/custom', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ name: toolsetName, functions: fns }),
            });
        }

        // If the selected toolset differs from the persona's current, update the persona
        // NOTE: PUT /api/personas/:name replaces settings entirely, so we must
        // fetch the full persona first and merge the toolset change.
        if (toolsetName !== (_editorPersona.toolset || 'conversation')) {
            const pResp = await fetch(`/api/personas/${encodeURIComponent(personaName)}`);
            const pData = await pResp.json();
            const fullSettings = pData?.settings || {};
            fullSettings.toolset = toolsetName;
            await fetch(`/api/personas/${encodeURIComponent(personaName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ settings: fullSettings }),
            });
        }

        _closeEditor();
        _loadPersonas(); // Refresh roster
    } catch (err) {
        console.error('[PA] Save toolset failed:', err);
    }
}

async function _editorSaveAs() {
    const newName = prompt('New toolset name:');
    if (!newName || !newName.trim()) return;

    const cleanName = newName.trim().toLowerCase().replace(/[^a-z0-9_]/g, '_');
    const fns = [..._editorCheckedFns];
    const personaName = _editorPersona?.name;

    try {
        // Create new toolset
        await fetch('/api/toolsets/custom', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
            body: JSON.stringify({ name: cleanName, functions: fns }),
        });

        // Assign to this persona (fetch full settings first, then merge)
        if (personaName) {
            const pResp = await fetch(`/api/personas/${encodeURIComponent(personaName)}`);
            const pData = await pResp.json();
            const fullSettings = pData?.settings || {};
            fullSettings.toolset = cleanName;
            await fetch(`/api/personas/${encodeURIComponent(personaName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF() },
                body: JSON.stringify({ settings: fullSettings }),
            });
        }

        _closeEditor();
        _loadPersonas(); // Refresh roster
    } catch (err) {
        console.error('[PA] Save As toolset failed:', err);
    }
}

function _closeEditor() {
    document.getElementById('pa-editor-overlay')?.remove();
    _editorPersona = null;
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function _esc(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ─── Styles ─────────────────────────────────────────────────────────────────

function _injectStyles() {
    if (document.getElementById('pa-styles')) return;
    const style = document.createElement('style');
    style.id = 'pa-styles';
    style.textContent = `
/* Persona Agents — Standalone View */
.pa-root {
    display: flex; flex-direction: column; height: 100%;
    background: #08080e; color: #ddd; font-family: system-ui, sans-serif;
}

/* Header */
.pa-header {
    display: flex; align-items: center; gap: 12px;
    padding: 12px 20px; border-bottom: 1px solid #1a1a24;
    flex-shrink: 0;
}
.pa-title { margin: 0; font-size: 1.1rem; font-weight: 700; }
.pa-subtitle { color: #666; font-size: 0.8rem; flex: 1; }
.pa-header-actions { display: flex; gap: 6px; }

/* Context bar */
.pa-context-row {
    display: flex; align-items: center; gap: 12px;
    padding: 6px 20px; border-bottom: 1px solid #1a1a24;
    flex-shrink: 0; font-size: 0.72rem;
}
.pa-ctx-item { color: #666; white-space: nowrap; }
.pa-ctx-item:empty { display: none; }
.pa-ctx-spacer { flex: 1; }

/* Lead / Toolset selectors */
.pa-ctx-selector { position: relative; display: flex; align-items: center; gap: 4px; }
.pa-ctx-icon { font-size: 0.85rem; opacity: 0.6; }
.pa-ctx-btn {
    display: flex; align-items: center; gap: 4px;
    background: none; border: 1px solid transparent; color: #aaa;
    padding: 3px 8px; border-radius: 4px; cursor: pointer;
    font-size: 0.78rem; font-weight: 600; transition: all 0.15s;
    white-space: nowrap;
}
.pa-ctx-btn:hover { color: #4a9eff; border-color: rgba(74,158,255,0.3); }
.pa-ctx-dropdown {
    position: absolute; top: 100%; left: 0; margin-top: 4px;
    background: #0e0e16; border: 1px solid #222; border-radius: 8px;
    min-width: 220px; max-height: 320px; overflow-y: auto;
    z-index: 25; box-shadow: 0 8px 24px rgba(0,0,0,0.6);
    padding: 4px;
}
.pa-ctx-dd-item {
    display: flex; align-items: center; gap: 8px;
    padding: 7px 10px; border-radius: 4px; cursor: pointer;
    font-size: 0.78rem; color: #aaa; transition: background 0.1s;
}
.pa-ctx-dd-item:hover { background: #1a1a24; color: #ddd; }
.pa-ctx-dd-active { color: #4a9eff; font-weight: 600; background: rgba(74,158,255,0.08); }
.pa-ctx-dd-avatar {
    width: 22px; height: 22px; border-radius: 50%;
    border: 2px solid #4a9eff; object-fit: cover; flex-shrink: 0;
}
.pa-ctx-dd-emoji { font-size: 0.9rem; flex-shrink: 0; width: 22px; text-align: center; }
.pa-ctx-dd-name { flex: 1; }
.pa-ctx-dd-count { font-size: 0.65rem; color: #555; flex-shrink: 0; }
.pa-ctx-dd-loading { padding: 12px; color: #555; font-size: 0.75rem; text-align: center; }

/* Context usage bar */
.pa-ctx-bar { display: flex; align-items: center; gap: 8px; min-width: 180px; }
.pa-ctx-track {
    flex: 1; height: 4px; background: #111118;
    border-radius: 3px; overflow: hidden;
}
.pa-ctx-fill {
    height: 100%; border-radius: 3px;
    transition: width 0.4s ease, background 0.4s ease;
    background: linear-gradient(90deg, #00bcd4, #4caf50);
}
.pa-ctx-label {
    font-size: 0.62rem; color: #555; white-space: nowrap;
    min-width: 60px; text-align: right; letter-spacing: 0.02em;
}
.pa-ctx-critical .pa-ctx-fill { animation: pa-pulse 1.5s ease-in-out infinite; }
.pa-ctx-critical .pa-ctx-label { color: #f44336; font-weight: 600; }

/* Chat selector */
.pa-chat-selector { position: relative; }
.pa-chat-toggle {
    display: flex; align-items: center; gap: 6px;
    font-size: 0.82rem; font-weight: 600; color: #ccc;
    background: #111118; border: 1px solid #222; padding: 5px 12px;
    border-radius: 6px; cursor: pointer; transition: all 0.15s;
    white-space: nowrap;
}
.pa-chat-toggle:hover { color: #4a9eff; border-color: #4a9eff; }
.pa-chat-dropdown {
    position: absolute; top: 100%; left: 0; margin-top: 4px;
    background: #0e0e16; border: 1px solid #222; border-radius: 8px;
    min-width: 200px; max-height: 300px; overflow-y: auto;
    z-index: 20; box-shadow: 0 8px 24px rgba(0,0,0,0.6);
}
.pa-chat-dd-header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 8px 12px 4px; border-bottom: 1px solid #1a1a24;
}
.pa-chat-dd-title { font-size: 0.75rem; font-weight: 700; color: #666; text-transform: uppercase; letter-spacing: 0.04em; }
.pa-chat-dropdown-list { padding: 4px; max-height: 200px; overflow-y: auto; }
.pa-chat-item {
    padding: 8px 12px; border-radius: 4px; font-size: 0.8rem;
    color: #aaa; cursor: pointer; transition: background 0.1s;
    display: flex; align-items: center; gap: 8px;
}
.pa-chat-item:hover { background: #1a1a24; color: #ddd; }
.pa-chat-item-active { color: #4a9eff; font-weight: 600; background: rgba(74,158,255,0.08); }
.pa-chat-item-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pa-chat-item-badge {
    font-size: 0.6rem; background: rgba(74,158,255,0.15); color: #4a9eff;
    padding: 1px 6px; border-radius: 8px; flex-shrink: 0;
}
.pa-chat-dd-footer {
    display: flex; gap: 4px; padding: 6px 8px;
    border-top: 1px solid #1a1a24;
}
.pa-chat-action { flex: 1; text-align: center; justify-content: center; }
.pa-chat-action:disabled { opacity: 0.3; cursor: not-allowed; pointer-events: none; }
.pa-chat-danger:hover { color: #f44336 !important; border-color: #f44336 !important; }
.pa-btn {
    background: #111118; border: 1px solid #222; color: #aaa;
    padding: 5px 12px; border-radius: 6px; cursor: pointer;
    font-size: 0.78rem; transition: all 0.15s;
}
.pa-btn:hover { color: #4a9eff; border-color: #4a9eff; }
.pa-btn-clear:hover { color: #f44336; border-color: #f44336; }
.pa-btn-sm { padding: 3px 8px; font-size: 0.72rem; }

/* Auto-continue toggle */
.pa-toggle {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 10px; font-size: 0.75rem; font-weight: 600;
}
.pa-toggle-dot {
    width: 10px; height: 10px; border-radius: 50%;
    background: #333; border: 1px solid #555;
    transition: all 0.25s ease;
}
.pa-toggle-dot.pa-toggle-on {
    background: #4caf50; border-color: #4caf50;
    box-shadow: 0 0 6px rgba(76, 175, 80, 0.5);
}
.pa-toggle-active {
    color: #4caf50 !important; border-color: rgba(76, 175, 80, 0.4) !important;
}

/* Thinking Orb — inline in context bar */
.pa-orb-wrap {
    display: flex; align-items: center; justify-content: center; gap: 6px;
    cursor: default;
}
.pa-orb-label {
    font-size: 0.65rem; font-weight: 600; color: #4a9eff;
    opacity: 0; transition: opacity 0.3s; pointer-events: none;
    letter-spacing: 0.03em;
}
.pa-orb-nudge .pa-orb-label {
    opacity: 1; cursor: pointer; pointer-events: auto;
}
.pa-orb {
    position: relative; width: 34px; height: 34px;
    display: flex; align-items: center; justify-content: center;
}
.pa-orb-ring {
    position: absolute; inset: 0; border-radius: 50%;
    border: 2.5px solid #222;
    transition: border-color 0.4s, opacity 0.4s;
}
.pa-orb-center {
    position: relative; z-index: 1;
    width: 24px; height: 24px; border-radius: 50%;
    background: #0e0e16; border: 1.5px solid #222;
    color: transparent; font-size: 0; font-weight: 700;
    cursor: default; transition: all 0.3s;
    display: flex; align-items: center; justify-content: center;
    padding: 0;
}

/* Idle — dim static ring */
.pa-orb-idle .pa-orb-ring {
    border-color: #333; opacity: 0.4;
}
.pa-orb-idle .pa-orb-center {
    background: #111118; border-color: #333;
}

/* Thinking — multicolor spinning ring */
.pa-orb-thinking .pa-orb-ring {
    border-color: transparent; opacity: 1;
    background: conic-gradient(
        #4a9eff 0deg, #a855f7 60deg, #ec4899 120deg,
        #f97316 180deg, #eab308 240deg, #22c55e 300deg, #4a9eff 360deg
    );
    -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #fff calc(100% - 2.5px));
    mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #fff calc(100% - 2.5px));
    animation: pa-orb-spin 1.5s linear infinite;
}
.pa-orb-thinking .pa-orb-center {
    background: #0a0a14; border-color: transparent;
    box-shadow: 0 0 8px rgba(74,158,255,0.15);
}

/* Nudge — spinning + pulsing ring, clickable center */
.pa-orb-nudge .pa-orb-ring {
    border-color: transparent; opacity: 1;
    background: conic-gradient(
        #4a9eff 0deg, #a855f7 60deg, #ec4899 120deg,
        #f97316 180deg, #eab308 240deg, #22c55e 300deg, #4a9eff 360deg
    );
    -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #fff calc(100% - 2.5px));
    mask: radial-gradient(farthest-side, transparent calc(100% - 3px), #fff calc(100% - 2.5px));
    animation: pa-orb-spin-pulse 2s ease-in-out infinite;
}
.pa-orb-nudge .pa-orb-center {
    background: linear-gradient(135deg, #1a3a5c, #1e2a44);
    border-color: rgba(74,158,255,0.5);
    cursor: pointer;
}
.pa-orb-nudge .pa-orb-center:hover {
    background: linear-gradient(135deg, #1e4a6e, #1e3a54);
    border-color: #4a9eff; color: #6ab5ff;
    box-shadow: 0 0 12px rgba(74,158,255,0.35);
    transform: scale(1.1);
}

@keyframes pa-orb-spin {
    to { transform: rotate(360deg); }
}
@keyframes pa-orb-spin-pulse {
    0%   { transform: rotate(0deg)   scale(1);    opacity: 0.65; }
    50%  { transform: rotate(180deg) scale(1.15);  opacity: 1;    }
    100% { transform: rotate(360deg) scale(1);    opacity: 0.65; }
}

/* Body — roster + transcript */
.pa-body {
    display: flex; flex: 1; overflow: hidden;
}

/* Roster Panel */
.pa-roster-panel {
    width: 240px; flex-shrink: 0; border-right: 1px solid #1a1a24;
    display: flex; flex-direction: column; overflow: hidden;
}
.pa-roster-title {
    padding: 12px 16px 8px; font-size: 0.82rem; font-weight: 700;
    color: #888; text-transform: uppercase; letter-spacing: 0.04em;
}
.pa-roster-list {
    flex: 1; overflow-y: auto; padding: 4px 10px;
    display: flex; flex-direction: column; gap: 6px;
}
.pa-roster-card {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 10px; background: #0a0a12; border-radius: 6px;
    border-left: 3px solid #4a9eff;
    cursor: pointer; transition: background 0.15s; position: relative;
}
.pa-roster-card:hover { background: #111120; }
.pa-roster-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    border: 2px solid #4a9eff; object-fit: cover; flex-shrink: 0;
}
.pa-roster-info { display: flex; flex-direction: column; min-width: 0; }
.pa-roster-name { font-weight: 700; font-size: 0.8rem; text-transform: capitalize; }
.pa-roster-tagline {
    font-size: 0.7rem; color: #666; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis;
}
.pa-roster-toolset { font-size: 0.65rem; color: #555; }
.pa-roster-empty { color: #555; font-size: 0.8rem; padding: 12px; }
.pa-loading { color: #555; font-size: 0.8rem; padding: 12px; }

/* Transcript Panel */
.pa-transcript-panel {
    flex: 1; display: flex; flex-direction: column; overflow: hidden;
}
.pa-transcript {
    flex: 1; overflow-y: auto; padding: 16px 24px;
    display: flex; flex-direction: column; gap: 12px;
}
.pa-transcript-empty {
    color: #555; text-align: center; padding: 60px 20px; font-size: 0.9rem;
}

/* Chat messages (conversation history) */
.pa-chat-msg {
    display: flex; animation: pa-fade-in 0.2s ease;
}
.pa-chat-user { justify-content: flex-end; }
.pa-chat-assistant { justify-content: flex-start; }
.pa-chat-bubble { max-width: 85%; border-radius: 10px; padding: 10px 14px; }
.pa-bubble-user {
    background: #1a1a2e; border: 1px solid #252540;
    border-radius: 10px 10px 2px 10px;
}
.pa-bubble-assistant {
    background: #0c0c16; border-left: 3px solid #4a9eff;
    border-radius: 2px 10px 10px 10px;
}
.pa-chat-role {
    font-size: 0.85rem; font-weight: 700; color: #888;
    display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
}
.pa-chat-avatar {
    width: 32px; height: 32px; border-radius: 50%;
    border: 2px solid #4a9eff; object-fit: cover; flex-shrink: 0;
}
.pa-chat-time {
    font-size: 0.62rem; font-weight: 400; color: #444;
    margin-left: auto; flex-shrink: 0;
}
.pa-chat-model {
    font-size: 0.6rem; font-weight: 400; color: #444;
    background: #111; padding: 1px 6px; border-radius: 4px;
}
.pa-chat-tools {
    font-size: 0.62rem; font-weight: 400; color: #666;
    background: rgba(74,158,255,0.08); padding: 1px 6px; border-radius: 4px;
}
.pa-chat-text {
    font-size: 0.85rem; line-height: 1.55; color: #ccc;
    word-break: break-word;
}
.pa-bubble-user .pa-chat-text { color: #aaa; font-size: 0.84rem; }
.pa-bubble-user .pa-chat-role { color: #666; }
.pa-bubble-user .pa-chat-avatar { border-color: #555; }

/* Dispatch notice */
.pa-system-msg {
    text-align: center; color: #777; font-size: 0.78rem; padding: 8px 12px;
    border-top: 1px solid #1a1a24; border-bottom: 1px solid #1a1a24;
}
.pa-tag {
    display: inline-block; background: #111; border: 1px solid #333;
    padding: 1px 6px; border-radius: 10px; font-size: 0.68rem; color: #888;
    margin-left: 4px;
}
.pa-task-preview {
    color: #555; font-size: 0.72rem; margin-top: 4px; font-style: italic;
}
.pa-time { color: #444; font-size: 0.68rem; margin-left: auto; }

/* Result message */
.pa-message {
    border-left: 3px solid #4a9eff; padding: 10px 14px; background: #0a0a12;
    border-radius: 0 8px 8px 0; animation: pa-fade-in 0.3s ease;
}
.pa-message-active {
    background: #0d0d16; border-left-style: dashed;
}
.pa-msg-header {
    display: flex; align-items: center; gap: 8px; margin-bottom: 6px;
}
.pa-msg-avatar {
    width: 28px; height: 28px; border-radius: 50%;
    border: 2px solid #4a9eff; object-fit: cover; flex-shrink: 0;
}
.pa-msg-name { font-weight: 700; font-size: 0.85rem; text-transform: capitalize; }
.pa-msg-meta { font-size: 0.72rem; color: #888; flex: 1; }
.pa-msg-content { font-size: 0.85rem; line-height: 1.6; color: #ccc; }

/* Chat input bar */
.pa-chat-bar {
    display: flex; align-items: flex-end; gap: 8px;
    padding: 10px 16px; border-top: 1px solid #1a1a24;
    background: #0a0a12; flex-shrink: 0;
}
.pa-chat-input {
    flex: 1; background: #111118; border: 1px solid #222; color: #ddd;
    padding: 10px 14px; border-radius: 8px; resize: none;
    font-family: system-ui, sans-serif; font-size: 0.85rem;
    line-height: 1.4; min-height: 20px; max-height: 120px;
    outline: none; transition: border-color 0.15s;
}
.pa-chat-input:focus { border-color: #4a9eff; }
.pa-chat-input::placeholder { color: #555; }
.pa-btn-send {
    background: #4a9eff; border: none; color: #fff; padding: 10px 16px;
    border-radius: 8px; cursor: pointer; font-size: 1rem;
    transition: background 0.15s; flex-shrink: 0;
}
.pa-btn-send:hover { background: #3a8eef; }

/* Bar TTS button (next to send) */
.pa-btn-tts-bar {
    background: #111118; border: 1px solid #222; color: #555;
    padding: 10px 14px; border-radius: 8px; cursor: pointer;
    font-size: 1rem; flex-shrink: 0; transition: all 0.15s;
}
.pa-btn-tts-bar:hover { color: #4a9eff; border-color: rgba(74,158,255,0.4); }
.pa-btn-tts-bar.pa-tts-loading { color: #ff9800; border-color: rgba(255,152,0,0.4); animation: pa-spin 1s linear infinite; }
.pa-btn-tts-bar.pa-tts-playing { color: #4a9eff; border-color: #4a9eff; animation: pa-pulse 1.2s ease-in-out infinite; }

/* TTS button */
.pa-tts-btn {
    background: none; border: 1px solid transparent; color: #555; cursor: pointer;
    font-size: 0.82rem; padding: 2px 6px; border-radius: 4px; transition: all 0.15s;
    flex-shrink: 0;
}
.pa-tts-btn:hover { color: #4a9eff; border-color: rgba(74,158,255,0.3); }
.pa-tts-loading { color: #ff9800; border-color: rgba(255,152,0,0.3); animation: pa-spin 1s linear infinite; }
.pa-tts-playing { color: #4a9eff; border-color: #4a9eff; animation: pa-pulse 1.2s ease-in-out infinite; }

/* Active typing dots */
.pa-typing { color: #888; font-size: 0.8rem; padding: 4px 0; }
.pa-typing-dots span {
    animation: pa-dot-bounce 1.4s infinite ease-in-out both; font-size: 1.2rem;
}
.pa-typing-dots span:nth-child(1) { animation-delay: 0s; }
.pa-typing-dots span:nth-child(2) { animation-delay: 0.2s; }
.pa-typing-dots span:nth-child(3) { animation-delay: 0.4s; }

/* Log Panel */
.pa-log-panel {
    position: absolute; top: 50px; right: 10px; bottom: 10px; width: 50%;
    background: #0a0a12; border: 1px solid #222; border-radius: 8px;
    display: flex; flex-direction: column; z-index: 10;
    box-shadow: 0 8px 32px rgba(0,0,0,0.5);
}
.pa-log-header {
    display: flex; align-items: center; gap: 8px; padding: 10px 14px;
    border-bottom: 1px solid #1a1a24; font-size: 0.82rem;
}
.pa-log-header h3 { margin: 0; font-size: 0.85rem; }
.pa-log-stats { flex: 1; text-align: right; font-size: 0.7rem; color: #555; }
.pa-log-content {
    flex: 1; overflow-y: auto; margin: 0; padding: 10px 14px;
    font-family: 'Consolas', 'Monaco', monospace; font-size: 0.72rem;
    line-height: 1.5; color: rgba(255,255,255,0.65); white-space: pre-wrap;
    word-break: break-word;
}

/* Message action buttons */
.pa-msg-actions {
    display: inline-flex; gap: 2px; margin-left: auto; opacity: 0;
    transition: opacity 0.15s;
}
.pa-chat-bubble:hover .pa-msg-actions { opacity: 1; }
.pa-action-btn {
    background: none; border: 1px solid transparent; color: #555;
    cursor: pointer; font-size: 0.72rem; padding: 2px 5px; border-radius: 4px;
    transition: all 0.15s; line-height: 1;
}
.pa-action-btn:hover { color: #4a9eff; border-color: rgba(74,158,255,0.3); }
.pa-delete-btn:hover { color: #ff5555; border-color: rgba(255,85,85,0.3); }
.pa-tts-loading { color: #ff9800 !important; animation: pa-spin 1s linear infinite; }
.pa-tts-playing { color: #4a9eff !important; animation: pa-pulse 1.2s ease-in-out infinite; }

/* Thinking blocks */
.pa-think-block {
    margin: 4px 0 6px; border-radius: 6px;
    background: rgba(255,255,255,0.03); border: 1px solid #1a1a2a;
}
.pa-think-summary {
    cursor: pointer; padding: 5px 10px; font-size: 0.72rem;
    color: #666; user-select: none; list-style: none;
}
.pa-think-summary::-webkit-details-marker { display: none; }
.pa-think-summary::before {
    content: '\u25B6'; display: inline-block; margin-right: 6px;
    font-size: 0.6rem; transition: transform 0.15s; color: #555;
}
details[open].pa-think-block > .pa-think-summary::before { transform: rotate(90deg); }
.pa-think-content {
    padding: 6px 12px 10px; font-size: 0.72rem; color: #777;
    line-height: 1.5; border-top: 1px solid #1a1a2a;
    font-style: italic; max-height: 300px; overflow-y: auto;
}
.pa-think-live {
    margin: 4px 0 6px; padding: 6px 10px; border-radius: 6px;
    background: rgba(74,158,255,0.04); border: 1px solid rgba(74,158,255,0.15);
    animation: pa-think-pulse 2s ease-in-out infinite;
}
.pa-think-live-label {
    font-size: 0.68rem; color: #4a7aaa; font-weight: 600;
    display: block; margin-bottom: 4px;
}
.pa-think-live-text {
    font-size: 0.72rem; color: #667; line-height: 1.5; font-style: italic;
}
@keyframes pa-think-pulse {
    0%, 100% { border-color: rgba(74,158,255,0.15); }
    50% { border-color: rgba(74,158,255,0.3); }
}

/* Roster card interactive */
.pa-roster-edit {
    position: absolute; right: 8px; top: 50%; transform: translateY(-50%);
    font-size: 0.9rem; color: #444; transition: color 0.15s; pointer-events: none;
}
.pa-roster-card:hover .pa-roster-edit { color: #888; }

/* ── Toolset Editor Modal ── */
#pa-editor-overlay {
    position: fixed; inset: 0; z-index: 9999;
    background: rgba(0,0,0,0.7); backdrop-filter: blur(4px);
    display: flex; align-items: center; justify-content: center;
    animation: pa-fade-in 0.15s ease;
}
.pa-editor-modal {
    width: 640px; max-width: 94vw; max-height: 85vh;
    background: #0c0c16; border: 1px solid #222; border-radius: 12px;
    display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 16px 64px rgba(0,0,0,0.6);
}
.pa-editor-header {
    display: flex; align-items: center; gap: 12px;
    padding: 16px 20px; border-bottom: 2px solid #222;
}
.pa-editor-avatar {
    width: 44px; height: 44px; border-radius: 50%;
    border: 2px solid #4a9eff; object-fit: cover; flex-shrink: 0;
}
.pa-editor-persona-info { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.pa-editor-name { font-weight: 700; font-size: 1rem; text-transform: capitalize; }
.pa-editor-tagline { font-size: 0.75rem; color: #666; margin-top: 2px; }
.pa-editor-close {
    background: none; border: none; color: #555; font-size: 1.2rem;
    cursor: pointer; padding: 4px 8px; border-radius: 4px; transition: color 0.15s;
}
.pa-editor-close:hover { color: #fff; }

.pa-editor-toolset-row {
    display: flex; align-items: center; gap: 10px;
    padding: 12px 20px; border-bottom: 1px solid #1a1a24;
}
.pa-editor-label { font-size: 0.75rem; color: #777; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.pa-editor-select {
    flex: 1; background: #111118; border: 1px solid #2a2a3a; color: #ccc;
    padding: 6px 10px; border-radius: 6px; font-size: 0.82rem; cursor: pointer;
    outline: none; transition: border-color 0.15s;
}
.pa-editor-select:focus { border-color: #4a9eff; }
.pa-editor-tool-count { font-size: 0.72rem; color: #4a9eff; font-weight: 600; white-space: nowrap; }

.pa-editor-search-row { padding: 8px 20px; }
.pa-editor-search {
    width: 100%; background: #111118; border: 1px solid #2a2a3a; color: #ccc;
    padding: 7px 12px; border-radius: 6px; font-size: 0.8rem; outline: none;
    transition: border-color 0.15s; box-sizing: border-box;
}
.pa-editor-search:focus { border-color: #4a9eff; }

.pa-editor-body {
    flex: 1; overflow-y: auto; padding: 4px 12px 12px;
}
.pa-fn-group { margin-bottom: 4px; }
.pa-fn-group-header {
    display: flex; align-items: center; gap: 8px;
    padding: 8px 8px 4px; position: sticky; top: 0;
    background: #0c0c16; z-index: 1;
}
.pa-fn-group-icon { font-size: 0.9rem; }
.pa-fn-group-name { font-size: 0.78rem; font-weight: 700; color: #aaa; text-transform: capitalize; flex: 1; }
.pa-fn-group-count { font-size: 0.68rem; color: #555; }
.pa-group-toggle {
    background: none; border: 1px solid #333; color: #555; cursor: pointer;
    font-size: 0.7rem; padding: 1px 6px; border-radius: 3px; transition: all 0.15s;
}
.pa-group-toggle:hover { color: #4a9eff; border-color: #4a9eff; }

.pa-fn-list { display: flex; flex-direction: column; gap: 1px; padding: 0 4px; }
.pa-fn-item {
    display: flex; align-items: center; gap: 8px;
    padding: 5px 8px; border-radius: 4px; cursor: pointer;
    transition: background 0.1s; font-size: 0.78rem;
}
.pa-fn-item:hover { background: #111120; }
.pa-fn-check { flex-shrink: 0; accent-color: #4a9eff; cursor: pointer; }
.pa-fn-name { font-weight: 600; color: #bbb; white-space: nowrap; min-width: 120px; }
.pa-fn-net { font-size: 0.65rem; flex-shrink: 0; }
.pa-fn-desc {
    font-size: 0.7rem; color: #555; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; flex: 1;
}

.pa-editor-footer {
    display: flex; align-items: center; justify-content: space-between;
    padding: 12px 20px; border-top: 1px solid #1a1a24;
}
.pa-editor-footer-left { flex: 1; }
.pa-editor-footer-right { display: flex; gap: 8px; }
.pa-editor-dirty {
    font-size: 0.72rem; color: #ff9800; opacity: 0; transition: opacity 0.2s;
}
.pa-editor-dirty.pa-visible { opacity: 1; }
.pa-editor-btn {
    padding: 7px 16px; border-radius: 6px; font-size: 0.8rem;
    cursor: pointer; transition: all 0.15s; border: 1px solid #333;
    background: #111118; color: #999;
}
.pa-editor-btn:hover { border-color: #555; color: #ccc; }
.pa-editor-btn-secondary { border-color: #2a4a6a; color: #6ab0ff; }
.pa-editor-btn-secondary:hover { border-color: #4a9eff; color: #4a9eff; background: rgba(74,158,255,0.08); }
.pa-editor-btn-primary { border-color: #4a9eff; color: #fff; background: #4a9eff; }
.pa-editor-btn-primary:hover { background: #3a8aef; }

/* Animations */
@keyframes pa-fade-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
@keyframes pa-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
@keyframes pa-spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
@keyframes pa-dot-bounce { 0%, 80%, 100% { opacity: 0.3; } 40% { opacity: 1; } }
    `;
    document.head.appendChild(style);
}
