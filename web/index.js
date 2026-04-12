// Persona Agents — Settings Tab (index.js)
// Registers a settings panel in the dashboard with memory mode selector
// and delegation configuration.

import { registerPluginSettings } from '/static/shared/plugin-registry.js';

const PLUGIN_NAME = 'persona-agents';
const API = '/api/plugin/persona-agents';

registerPluginSettings({
    id: PLUGIN_NAME,
    name: 'Persona Agents',
    icon: '\uD83C\uDFAD',
    helpText: 'Delegation system — team management and memory integration',

    render(container, settings) {
        // ── About section ──────────────────────────────────────────────
        const aboutHTML = `
            <div style="margin-bottom:20px; padding:16px; background:var(--bg-secondary, #1a1a2e); border-radius:8px; border:1px solid var(--border, #333)">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:12px">
                    <span style="font-size:1.8em">\uD83C\uDFAD</span>
                    <div style="flex:1">
                        <h3 style="margin:0; font-size:1.1em; color:var(--text-primary, #fff)">Persona Agents</h3>
                        <span style="font-size:0.85em; color:var(--text-secondary, #aaa)">Delegate tasks to persona-powered specialists</span>
                    </div>
                </div>
                <div style="font-size:0.9em; color:var(--text-secondary, #ccc); line-height:1.6">
                    <p style="margin:0 0 10px">
                        Your lead persona delegates tasks to specialist agents, each running with their own
                        <strong>personality</strong>, <strong>toolset</strong>, and <strong>LLM provider</strong>.
                        Results include character flair and structured reporting.
                    </p>
                </div>
            </div>
        `;

        // ── MemPalace Integration section ──────────────────────────────
        const mempalaceDetected = settings?.mempalace_detected || false;
        const currentMode = settings?.memory_mode || 'auto';

        const statusDot = mempalaceDetected
            ? '<span style="color:#4ade80">●</span> Detected'
            : '<span style="color:#666">●</span> Not detected';

        const mempalaceHTML = `
            <div style="margin-bottom:20px; padding:16px; background:var(--bg-secondary, #1a1a2e); border-radius:8px; border:1px solid var(--border, #333)">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px">
                    <span style="font-size:1.4em">\uD83E\uDDE0</span>
                    <div style="flex:1">
                        <h4 style="margin:0; font-size:1em; color:var(--text-primary, #fff)">MemPalace Integration</h4>
                        <span style="font-size:0.82em; color:var(--text-secondary, #aaa)">
                            Memory system for delegated agents — ${statusDot}
                        </span>
                    </div>
                </div>

                <div style="margin-bottom:14px">
                    <label style="display:block; margin-bottom:6px; font-size:0.9em; color:var(--text-primary, #fff); font-weight:500">
                        Delegate Memory Mode
                    </label>
                    <select id="pa-memory-mode" style="
                        width:100%; padding:8px 12px; border-radius:6px;
                        border:1px solid var(--border, #444); background:var(--bg-primary, #0f0f23);
                        color:var(--text-primary, #fff); font-size:0.9em; cursor:pointer;
                    ">
                        <option value="auto" ${currentMode === 'auto' ? 'selected' : ''}>
                            Auto — Use MemPalace if installed, fall back to standard
                        </option>
                        <option value="mempalace" ${currentMode === 'mempalace' ? 'selected' : ''}>
                            MemPalace — Always use MemPalace memory tools
                        </option>
                        <option value="standard" ${currentMode === 'standard' ? 'selected' : ''}>
                            Standard — Always use classic memory tools
                        </option>
                        <option value="none" ${currentMode === 'none' ? 'selected' : ''}>
                            None — Delegates get no memory tools
                        </option>
                    </select>
                    <div style="margin-top:6px; font-size:0.8em; color:var(--text-secondary, #888); line-height:1.5">
                        <strong>Auto</strong> is recommended. When MemPalace is detected, delegates automatically get
                        memory injection (L0/L1/L2) and MemPalace tools instead of the old memory system.
                    </div>
                </div>

                <div id="pa-mp-status" style="
                    padding:10px 14px; border-radius:6px; font-size:0.85em; line-height:1.5;
                    background:${mempalaceDetected ? 'rgba(74,222,128,0.08)' : 'rgba(255,255,255,0.03)'};
                    border:1px solid ${mempalaceDetected ? 'rgba(74,222,128,0.2)' : 'var(--border, #333)'};
                    color:var(--text-secondary, #aaa);
                ">
                    ${mempalaceDetected
                        ? `<strong style="color:#4ade80">\u2713 MemPalace active.</strong> Delegates will receive their persona's memories (L0 Identity, L1 Essential Knowledge, L2 Context) and can use <code>memory_remember</code>, <code>memory_recall</code>, <code>memory_search</code>, and <code>memory_diary</code>.`
                        : `<strong>MemPalace not detected.</strong> Delegates will use the standard memory system. Install and enable the MemPalace plugin for enhanced per-persona memory.`
                    }
                </div>
            </div>
        `;

        container.innerHTML = aboutHTML + mempalaceHTML;

        // ── Event listeners ────────────────────────────────────────────
        const modeSelect = container.querySelector('#pa-memory-mode');
        if (modeSelect) {
            modeSelect.addEventListener('change', async () => {
                try {
                    const resp = await fetch(`${API}/settings`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ memory_mode: modeSelect.value }),
                    });
                    if (resp.ok) {
                        const toast = window.ui?.showToast || window.showToast;
                        if (toast) toast('Memory mode updated', 'success');
                    }
                } catch (e) {
                    console.error('[PA Settings] Failed to save memory mode:', e);
                }
            });
        }
    },

    async load() {
        try {
            const resp = await fetch(`${API}/settings`);
            if (resp.ok) return await resp.json();
        } catch {}
        return {};
    },

    getSettings() {
        const mode = document.querySelector('#pa-memory-mode')?.value || 'auto';
        return { memory_mode: mode };
    },

    async save(settings) {
        try {
            const resp = await fetch(`${API}/settings`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings),
            });
            if (resp.ok) return await resp.json();
        } catch {}
        return {};
    },
});
