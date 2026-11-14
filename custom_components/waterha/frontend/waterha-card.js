/* WaterHA card: rain either side of now, and every tap with its controls.
 *
 *   type: custom:waterha-card
 *   title: Garden            # optional
 *   entity: sensor.xxx       # optional; the "Rain past and next 24 h" sensor
 *
 * No build step: plain custom element, styled with Home Assistant theme vars.
 */
const VERSION = "0.2.1";

const BAND = {
  cool: ["Cool", "mdi:snowflake"],
  warm: ["Warm", "mdi:weather-partly-cloudy"],
  hot: ["Hot", "mdi:white-balance-sunny"],
};

// One-tap choices when editing a value; any other value can be typed.
const PRESETS = {
  min: [5, 10, 15, 20, 30, 45, 60],
  L: [10, 20, 50, 100, 150, 200, 300],
  mm: [0, 2, 5, 10, 15, 20, 30],
};

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
const fmt = (v) => {
  const n = parseFloat(v);
  return isNaN(n) ? "–" : String(Math.round(n * 10) / 10);
};
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
      this.shadowRoot.addEventListener("change", (ev) => this._typed(ev));
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Escape") this._closeEdit();
        else if (ev.key === "Enter" && ev.target.dataset?.input) ev.target.blur();
      });
      this.shadowRoot.addEventListener("focusout", () => {
        if (this._pending) setTimeout(() => this._flush(), 0);
      });
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
    const a0 = rain?.attributes || {};
    ids.push(a0.cool_below_entity, a0.hot_from_entity);
    for (const t of taps) {
      ids.push(t.status, t.next_run, t.automatic, t.one_at_a_time, t.amount_cool, t.amount_warm,
        t.amount_hot, t.rain_threshold, t.start_time);
    }
    const sig = ids.map((id) => {
      const s = id && hass.states[id];
      return s ? `${s.state}|${s.last_updated}` : "";
    }).join(",");
    if (sig === this._sig) return;
    this._sig = sig;
    this._taps = taps;
    // Never redraw under someone typing a value; redraw when they leave the box.
    if (this.shadowRoot.activeElement?.dataset?.input) {
      this._pending = true;
      return;
    }
    this._render(rain, taps);
  }

  _flush() {
    if (this.shadowRoot.activeElement?.dataset?.input) return;
    this._pending = false;
    this._render(this._rainState(), this._taps || []);
  }

  _closeEdit() {
    if (!this._edit) return;
    this._edit = null;
    this._pending = false;
    this._render(this._rainState(), this._taps || []);
  }

  _setNumber(entityId, raw) {
    const s = this._hass.states[entityId];
    const v = parseFloat(raw);
    if (!s || isNaN(v)) return;
    const a = s.attributes;
    const value = Math.min(a.max ?? 9999, Math.max(a.min ?? 0, v));
    this._edit = null;
    this._hass.callService("number", "set_value", { entity_id: entityId, value });
    this._render(this._rainState(), this._taps || []);
  }

  _editor(entityId, unit, label) {
    const s = this._hass.states[entityId];
    if (!s) return "";
    const cur = parseFloat(s.state);
    const a = s.attributes;
    const chips = PRESETS[unit]
      .filter((v) => v >= (a.min ?? 0) && v <= (a.max ?? 9999))
      .map((v) => `<button class="chip${v === cur ? " on" : ""}" data-set="${esc(entityId)}" data-v="${v}">${unit === "mm" && v === 0 ? "Off" : v}</button>`)
      .join("");
    return `<div class="editor">
      <span class="elabel">${esc(label || (unit === "mm" ? "Skip when rain (past + next 24 h) reaches; less rain shortens runs" : "Water for"))}</span>
      <div class="chips">${chips}
        <span class="custom"><input type="number" inputmode="decimal" data-input="${esc(entityId)}"
          min="${a.min ?? 0}" max="${a.max ?? 9999}" step="${a.step ?? 1}" placeholder="${esc(fmt(s.state))}"
          aria-label="Other value"/> ${unit}</span>
        <button class="chip done" data-close="1" aria-label="Close">✕</button>
      </div>
    </div>`;
  }

  _splitsEditor(a) {
    const input = (id, v) => `<input type="number" inputmode="decimal" step="0.5" data-input="${esc(id)}"
      placeholder="${esc(fmt(v))}" aria-label="°C"/>`;
    return `<div class="editor">
      <span class="elabel">Day type from today's forecast high (all taps)</span>
      <div class="chips">
        <span class="custom"><ha-icon icon="mdi:snowflake"></ha-icon> cool below ${input(a.cool_below_entity, a.cool_below)} °C</span>
        <span class="custom"><ha-icon icon="mdi:white-balance-sunny"></ha-icon> hot from ${input(a.hot_from_entity, a.hot_from)} °C</span>
        <button class="chip done" data-close="1" aria-label="Close">✕</button>
      </div>
    </div>`;
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
          <button class="stat today${this._edit === "__splits" ? " open" : ""}" data-edit="__splits" title="Change the cool / warm / hot split">
            <span>Today</span><b><ha-icon icon="${BAND[a.day_type]?.[1] || "mdi:thermometer"}"></ha-icon>${esc(BAND[a.day_type]?.[0] || "–")}${a.temp_max_today != null ? ` ${fmt(a.temp_max_today)}°` : ""}</b>
          </button>
        </div>
        ${this._edit === "__splits" ? this._splitsEditor(a) : ""}
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
    const thr = st[t.rain_threshold]?.state;
    const auto = st[t.automatic]?.state === "on";
    const seq = st[t.one_at_a_time]?.state === "on";
    const amounts = ["cool", "warm", "hot"].map((b) => {
      const id = t[`amount_${b}`];
      return `<button class="val band${a.day_type === b ? " today" : ""}${this._edit === id ? " open" : ""}" data-edit="${esc(id)}"
        title="${BAND[b][0]} day amount${a.day_type === b ? " (today)" : ""}"><ha-icon icon="${BAND[b][1]}"></ha-icon>${esc(fmt(st[id]?.state))}</button>`;
    }).join("");
    const editing = ["cool", "warm", "hot"].find((b) => this._edit === t[`amount_${b}`]);
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
    const factor = a.rain_factor ?? 1;
    const rainNote = !(thrNum > 0)
      ? "ignores rain"
      : factor <= 0
        ? `skipping: ${isNaN(rainNum) ? "" : rainNum.toFixed(1)} of ${fmt(thrNum)} mm rain`
        : factor < 1
          ? `−${Math.round((1 - factor) * 100)}% for ${rainNum.toFixed(1)} mm rain`
          : `skips at ${fmt(thrNum)} mm`;
    const plan = factor > 0 && a.scheduled_amount != null
      ? ` · ${fmt(a.scheduled_amount)} ${unit} (${(BAND[a.day_type]?.[0] || "").toLowerCase()} day)`
      : "";
    const last = a.last_result
      ? `Last: ${esc(RESULT[a.last_result] || a.last_result)}${a.last_run ? ` ${esc(when(a.last_result === "skipped_rain" ? a.last_skip : a.last_run))}` : ""}${a.last_litres != null ? ` · ${a.last_litres} L` : a.last_minutes != null ? ` · ${a.last_minutes} min` : ""}`
      : "Not run yet";
    return `<div class="tap">
      <div class="row">
        <button class="name" data-more="${esc(t.status)}">${esc(t.name)}</button>
        <span class="pill" style="--c:${color}">${esc(label)}${s.state === "queued" && a.queue_position ? ` #${a.queue_position}` : ""}</span>
        <span class="grow"></span>
        ${run || s.state === "queued"
          ? `<button class="act stop" data-press="${esc(t.stop)}"><ha-icon icon="mdi:stop"></ha-icon>Stop</button>`
          : `<button class="act" data-press="${esc(t.water_now)}"><ha-icon icon="mdi:water"></ha-icon>Water now</button>`}
      </div>
      ${progress}
      <div class="row ctl">
        <label class="sw"><input type="checkbox" data-toggle="${esc(t.automatic)}" ${auto ? "checked" : ""}/> Auto</label>
        <label class="sw" title="Wait for other one-at-a-time taps to finish"><input type="checkbox" data-toggle="${esc(t.one_at_a_time)}" ${seq ? "checked" : ""}/> One at a time</label>
      </div>
      <div class="row ctl">
        <span class="trio" title="Cool / warm / hot day amounts (${unit})">${amounts}<span class="unit">${unit}</span></span>
        <button class="val${this._edit === t.rain_threshold ? " open" : ""}" data-edit="${esc(t.rain_threshold)}" title="Rain threshold (past + next 24 h)">
          <ha-icon icon="mdi:weather-pouring"></ha-icon>${thrNum > 0 ? `${esc(fmt(thr))} mm` : "off"}</button>
        <button class="val" data-more="${esc(t.start_time)}" title="Start time">
          <ha-icon icon="mdi:clock-outline"></ha-icon>${esc((a.start_time || "").slice(0, 5))}</button>
      </div>
      ${editing ? this._editor(t[`amount_${editing}`], unit, `Water on a ${BAND[editing][0].toLowerCase()} day for`) : ""}
      ${this._edit === t.rain_threshold ? this._editor(t.rain_threshold, "mm") : ""}
      <div class="sub">${auto && a.next_run ? `Next ${esc(when(a.next_run))}${esc(plan)}` : auto ? "No days selected" : "Automatic watering is off"} · ${esc(rainNote)}</div>
      <div class="sub">${last}</div>
    </div>`;
  }

  _typed(ev) {
    const id = ev.target?.dataset?.input;
    if (id && ev.target.value !== "") this._setNumber(id, ev.target.value);
  }

  _click(ev) {
    const el = ev.composedPath().find((n) => n.dataset && (n.dataset.press || n.dataset.set || n.dataset.edit || n.dataset.close || n.dataset.more || n.dataset.toggle || n.dataset.act));
    if (!el || !this._hass) return;
    const d = el.dataset;
    if (d.press) this._hass.callService("button", "press", { entity_id: d.press });
    else if (d.act === "refresh") this._hass.callService("waterha", "refresh_rain", {});
    else if (d.toggle) {
      ev.preventDefault();
      const on = this._hass.states[d.toggle]?.state === "on";
      this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: d.toggle });
    } else if (d.set) {
      this._setNumber(d.set, d.v);
    } else if (d.edit) {
      this._edit = this._edit === d.edit ? null : d.edit;
      this._render(this._rainState(), this._taps || []);
    } else if (d.close) {
      this._closeEdit();
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
  .rain { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 8px 0 4px; }
  .stat.today { text-align: left; border-radius: 8px; --mdc-icon-size: 18px; }
  .stat.today b { display: inline-flex; align-items: center; gap: 3px; white-space: nowrap; }
  .stat.today.open b, .stat.today:hover b { color: var(--primary-color); }
  .trio { display: inline-flex; align-items: center; gap: 4px; }
  .trio .unit { font-size: 0.85em; color: var(--secondary-text-color); }
  .val.band { padding: 0 8px; }
  .val.today { border-color: var(--primary-color); border-width: 2px; font-weight: 500; }
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
  .val { display: inline-flex; align-items: center; gap: 4px; min-height: 32px; padding: 0 10px;
         border: 1px solid var(--divider-color); border-radius: 16px; --mdc-icon-size: 18px; }
  .val ha-icon { color: var(--secondary-text-color); }
  .val.open { border-color: var(--primary-color); color: var(--primary-color); }
  .editor { margin-top: 8px; padding: 8px; border-radius: 12px;
            background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  .elabel { font-size: 0.8em; color: var(--secondary-text-color); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; align-items: center; }
  .chip { min-width: 40px; min-height: 34px; padding: 0 10px; border-radius: 17px;
          border: 1px solid var(--divider-color); background: var(--card-background-color, transparent); }
  .chip.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
  .chip.done { min-width: 34px; color: var(--secondary-text-color); }
  .custom { display: inline-flex; align-items: center; gap: 4px; color: var(--secondary-text-color); }
  .custom input { width: 64px; min-height: 32px; padding: 0 8px; border-radius: 16px; font: inherit;
                  border: 1px solid var(--divider-color); background: var(--card-background-color, transparent);
                  color: var(--primary-text-color); }
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
