/* WaterHA card: rain either side of now, and every tap with its controls.
 *
 *   type: custom:waterha-card
 *   title: Garden            # optional
 *   entity: sensor.xxx       # optional; the "Rain past and next 24 h" sensor
 *
 * No build step: plain custom element, styled with Home Assistant theme vars.
 */
const VERSION = "0.1.0";

const STATUS = {
  idle: ["Idle", "var(--secondary-text-color)"],
  watering: ["Watering", "var(--info-color, #039be5)"],
  queued: ["Waiting", "var(--warning-color, #ffa600)"],
  disabled: ["Automatic off", "var(--disabled-text-color, #9e9e9e)"],
  rain_hold: ["Holding for rain", "var(--success-color, #43a047)"],
};
const RESULT = {
  completed: "done",
  max_time: "stopped at the time limit",
  no_flow: "stopped, no flow",
  stopped: "stopped",
  stopped_externally: "closed elsewhere",
  interrupted_by_restart: "interrupted by a restart",
  skipped_rain: "skipped for rain",
  failed: "tap did not open",
};

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const mm = (v) => (v === null || v === undefined || isNaN(v) ? "–" : `${(+v).toFixed(1)} mm`);

function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const today = new Date();
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const diff = Math.round((day - base) / 86400000);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (diff === 0) return `today ${time}`;
  if (diff === 1) return `tomorrow ${time}`;
  if (diff === -1) return `yesterday ${time}`;
  return `${d.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}

class WaterHACard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._sig = null;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => this._click(ev));
    }
  }

  static getStubConfig() {
    return { title: "Garden watering" };
  }

  getCardSize() {
    return 3 + (this._taps?.length || 0) * 2;
  }

  set hass(hass) {
    this._hass = hass;
    const rain = this._rainState();
    const taps = rain?.attributes?.taps || [];
    const ids = [rain?.entity_id];
    for (const t of taps) ids.push(t.status, t.next_run, t.automatic, t.amount, t.rain_threshold, t.start_time);
    const sig = ids.map((id) => {
      const s = id && hass.states[id];
      return s ? `${s.state}|${s.last_updated}` : "";
    }).join(",");
    if (sig === this._sig) return;
    this._sig = sig;
    this._taps = taps;
    this._render(rain, taps);
  }

  _rainState() {
    const states = this._hass.states;
    if (this._config.entity) return states[this._config.entity];
    return Object.values(states).find((s) => s.attributes && s.attributes.waterha === "rain");
  }

  _render(rain, taps) {
    const root = this.shadowRoot;
    const title = this._config.title ?? "Garden watering";
    if (!rain) {
      root.innerHTML = `${STYLE}<ha-card header="${esc(title)}"><div class="empty">
        WaterHA is not set up yet, or its rain sensor is disabled. Add the WaterHA integration, then add taps from its page.
      </div></ha-card>`;
      return;
    }
    const a = rain.attributes;
    const total = rain.state;
    root.innerHTML = `${STYLE}
      <ha-card>
        <div class="head">
          <div class="title">${esc(title)}</div>
          <button class="icon" data-act="refresh" title="Refresh rain"><ha-icon icon="mdi:refresh"></ha-icon></button>
        </div>
        <div class="rain">
          <div class="stat"><span>Past 24 h</span><b>${mm(a.past_24h)}</b></div>
          <div class="stat"><span>Next 24 h</span><b>${mm(a.next_24h)}</b></div>
          <div class="stat total"><span>Total</span><b>${mm(total === "unknown" || total === "unavailable" ? null : total)}</b></div>
        </div>
        ${this._chart(a.hours || [])}
        <div class="src">${a.error ? `<span class="err">${esc(a.error)}</span>` : esc(a.source || "")}${a.updated ? ` · updated ${esc(when(a.updated))}` : ""}</div>
        <div class="taps">
          ${taps.length ? taps.map((t) => this._tap(t, total)).join("") : `<div class="empty">No taps yet. Open Settings → Devices & services → WaterHA and choose <b>Add tap</b>.</div>`}
        </div>
      </ha-card>`;
  }

  _chart(hours) {
    if (!hours.length) return `<div class="chart empty">No hourly rain data</div>`;
    const W = 480, H = 84, pad = 14;
    const now = Date.now();
    const max = Math.max(1, ...hours.map((h) => h.mm));
    const bw = W / hours.length;
    let nowX = null;
    const bars = hours.map((h, i) => {
      const end = new Date(h.t).getTime();
      const past = end <= now;
      if (!past && nowX === null) nowX = i * bw + bw * Math.min(1, Math.max(0, 1 - (end - now) / 3600000));
      const bh = h.mm > 0 ? Math.max(2, (h.mm / max) * (H - pad)) : 0;
      const op = past ? 0.45 : 0.55 + 0.45 * ((h.p ?? 100) / 100);
      return `<rect x="${(i * bw + 0.5).toFixed(1)}" y="${(H - bh).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${bh.toFixed(1)}"
        rx="1" class="${past ? "past" : "next"}" opacity="${op.toFixed(2)}"><title>${esc(when(h.t))}: ${h.mm} mm${h.p != null ? `, ${h.p}%` : ""}</title></rect>`;
    }).join("");
    const nowLine = nowX === null ? "" : `<line x1="${nowX}" x2="${nowX}" y1="0" y2="${H}" class="now"/><text x="${nowX + 3}" y="9" class="lbl">now</text>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H + 12}" preserveAspectRatio="none" role="img" aria-label="Hourly rain, past and next 24 hours">
      <line x1="0" x2="${W}" y1="${H}" y2="${H}" class="axis"/>${bars}${nowLine}
      <text x="0" y="${H + 11}" class="lbl">−24 h</text><text x="${W}" y="${H + 11}" class="lbl" text-anchor="end">+24 h</text>
      <text x="${W}" y="9" class="lbl" text-anchor="end">max ${max.toFixed(1)} mm/h</text>
    </svg>`;
  }

  _tap(t, total) {
    const st = this._hass.states;
    const s = st[t.status];
    if (!s) return "";
    const a = s.attributes;
    const [label, color] = STATUS[s.state] || [s.state, "var(--secondary-text-color)"];
    const unit = a.mode === "litres" ? "L" : "min";
    const amount = st[t.amount]?.state;
    const thr = st[t.rain_threshold]?.state;
    const auto = st[t.automatic]?.state === "on";
    const run = a.run;
    let progress = "";
    if (run) {
      const done = run.mode === "litres" ? run.litres : run.elapsed_minutes;
      const pct = Math.min(100, (100 * done) / (run.target || 1));
      progress = `<div class="bar"><div style="width:${pct.toFixed(0)}%"></div></div>
        <div class="sub">${done.toFixed(1)} of ${run.target} ${run.mode === "litres" ? "L" : "min"}${run.flow_lpm ? ` · ${run.flow_lpm} L/min` : ""}</div>`;
    }
    const thrNum = parseFloat(thr);
    const rainNum = parseFloat(total);
    const rainNote = thrNum > 0
      ? `skips at ${thrNum} mm${!isNaN(rainNum) ? ` (now ${rainNum.toFixed(1)})` : ""}`
      : "never skips for rain";
    const last = a.last_result
      ? `Last: ${esc(RESULT[a.last_result] || a.last_result)}${a.last_run ? ` ${esc(when(a.last_result === "skipped_rain" ? a.last_skip : a.last_run))}` : ""}${a.last_litres != null ? ` · ${a.last_litres} L` : a.last_minutes != null ? ` · ${a.last_minutes} min` : ""}`
      : "Not run yet";
    return `<div class="tap">
      <div class="row">
        <button class="name" data-more="${esc(t.status)}">${esc(t.name)}</button>
        <span class="pill" style="--c:${color}">${esc(label)}</span>
        <span class="grow"></span>
        ${run || s.state === "queued"
          ? `<button class="act stop" data-press="${esc(t.stop)}"><ha-icon icon="mdi:stop"></ha-icon>Stop</button>`
          : `<button class="act" data-press="${esc(t.water_now)}"><ha-icon icon="mdi:water"></ha-icon>Water now</button>`}
      </div>
      ${progress}
      <div class="row ctl">
        <label class="sw"><input type="checkbox" data-toggle="${esc(t.automatic)}" ${auto ? "checked" : ""}/> Auto</label>
        <span class="step">
          <button data-num="${esc(t.amount)}" data-d="${unit === "L" ? -5 : -1}">−</button>
          <span>${esc(amount)} ${unit}</span>
          <button data-num="${esc(t.amount)}" data-d="${unit === "L" ? 5 : 1}">+</button>
        </span>
        <span class="step" title="Rain threshold (past + next 24 h)">
          <ha-icon icon="mdi:weather-pouring"></ha-icon>
          <button data-num="${esc(t.rain_threshold)}" data-d="-1">−</button>
          <span>${esc(thr)} mm</span>
          <button data-num="${esc(t.rain_threshold)}" data-d="1">+</button>
        </span>
        <button class="time" data-more="${esc(t.start_time)}">${esc((a.start_time || "").slice(0, 5))}</button>
      </div>
      <div class="sub">${auto && a.next_run ? `Next ${esc(when(a.next_run))}` : auto ? "No days selected" : "Automatic watering is off"} · ${esc(rainNote)}</div>
      <div class="sub">${last}</div>
    </div>`;
  }

  _click(ev) {
    const el = ev.composedPath().find((n) => n.dataset && (n.dataset.press || n.dataset.num || n.dataset.more || n.dataset.toggle || n.dataset.act));
    if (!el || !this._hass) return;
    const d = el.dataset;
    if (d.press) this._hass.callService("button", "press", { entity_id: d.press });
    else if (d.act === "refresh") this._hass.callService("waterha", "refresh_rain", {});
    else if (d.toggle) {
      ev.preventDefault();
      const on = this._hass.states[d.toggle]?.state === "on";
      this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: d.toggle });
    } else if (d.num) {
      const s = this._hass.states[d.num];
      if (!s) return;
      const a = s.attributes;
      const v = Math.min(a.max ?? 9999, Math.max(a.min ?? 0, parseFloat(s.state) + parseFloat(d.d)));
      this._hass.callService("number", "set_value", { entity_id: d.num, value: v });
    } else if (d.more) {
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: d.more }, bubbles: true, composed: true }));
    }
  }
}

const STYLE = `<style>
  ha-card { padding: 12px 16px 8px; }
  .head { display: flex; align-items: center; }
  .title { font-size: 1.25em; font-weight: 500; flex: 1; }
  button { font: inherit; color: inherit; background: none; border: 0; cursor: pointer; padding: 0; }
  .icon { color: var(--secondary-text-color); padding: 4px; border-radius: 50%; }
  .rain { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 8px 0 4px; }
  .stat { display: flex; flex-direction: column; }
  .stat span { font-size: 0.8em; color: var(--secondary-text-color); }
  .stat b { font-size: 1.2em; font-weight: 500; }
  .stat.total b { color: var(--primary-color); }
  .chart { width: 100%; height: 96px; display: block; }
  .chart.empty { height: auto; color: var(--secondary-text-color); font-size: 0.85em; padding: 8px 0; }
  .chart .past { fill: var(--secondary-text-color); }
  .chart .next { fill: var(--info-color, #039be5); }
  .chart .axis { stroke: var(--divider-color); stroke-width: 1; }
  .chart .now { stroke: var(--primary-text-color); stroke-dasharray: 3 3; stroke-width: 1; }
  .chart .lbl { font-size: 9px; fill: var(--secondary-text-color); }
  .src { font-size: 0.75em; color: var(--secondary-text-color); margin-bottom: 4px; }
  .err { color: var(--error-color); }
  .taps { border-top: 1px solid var(--divider-color); }
  .tap { padding: 10px 0; border-bottom: 1px solid var(--divider-color); }
  .tap:last-child { border-bottom: 0; }
  .row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .grow { flex: 1; }
  .name { font-weight: 500; font-size: 1.05em; }
  .pill { font-size: 0.75em; padding: 2px 8px; border-radius: 10px; color: var(--c);
          border: 1px solid var(--c); white-space: nowrap; }
  .act { display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border-radius: 16px;
         background: var(--primary-color); color: var(--text-primary-color, #fff); --mdc-icon-size: 18px; }
  .act.stop { background: var(--error-color, #db4437); }
  .ctl { margin-top: 6px; gap: 12px; }
  .step { display: inline-flex; align-items: center; gap: 4px; --mdc-icon-size: 18px; color: var(--primary-text-color); }
  .step button { width: 26px; height: 26px; border-radius: 50%; border: 1px solid var(--divider-color); line-height: 1; }
  .time { border: 1px solid var(--divider-color); border-radius: 12px; padding: 2px 8px; }
  .sw { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
  .sub { font-size: 0.8em; color: var(--secondary-text-color); margin-top: 3px; }
  .bar { height: 6px; background: var(--divider-color); border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .bar div { height: 100%; background: var(--info-color, #039be5); }
  .empty { padding: 12px 0; color: var(--secondary-text-color); }
</style>`;

if (!customElements.get("waterha-card")) {
  customElements.define("waterha-card", WaterHACard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "waterha-card",
    name: "WaterHA",
    description: "Rain past and next 24 h, and your automated taps.",
  });
  console.info(`%c WaterHA card ${VERSION} `, "background:#039be5;color:#fff");
}
