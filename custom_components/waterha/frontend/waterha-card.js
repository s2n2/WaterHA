/* WaterHA cards (main, history, cost): rain either side of now, and every tap with its controls.
 *
 *   type: custom:waterha-card
 *   title: Garden            # optional
 *   entity: sensor.xxx       # optional; the "Rain past and next 24 h" sensor
 *
 * No build step: plain custom element, styled with Home Assistant theme vars.
 */
const VERSION = "0.6.0";
const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
const WHEN = { always: "Every day", warm_hot: "Warm & hot days", hot: "Hot days only" };
const SHARES = [100, 75, 50, 30, 20];

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
    ids.push(a0.cool_below_entity, a0.hot_from_entity, a0.et_cool_below_entity, a0.et_hot_from_entity);
    for (const t of taps) {
      ids.push(t.status, t.next_run, t.automatic, t.one_at_a_time, t.amount_cool, t.amount_warm,
        t.amount_hot, t.rain_threshold, t.start_time, t.start_time_2, t.slot_1, t.slot_2,
        t.slot_1_when, t.slot_2_when, t.slot_1_share, t.slot_2_share);
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
    const input = (id, v, unit) => `<input type="number" inputmode="decimal" step="0.5" data-input="${esc(id)}"
      placeholder="${esc(fmt(v))}" aria-label="${unit}"/>`;
    return `<div class="editor">
      <span class="elabel">Day type, for all taps: the hotter of today's forecast high and evapotranspiration
        (water pulled out by heat, sun, wind and dry air)</span>
      <div class="chips">
        <span class="custom"><ha-icon icon="mdi:snowflake"></ha-icon> cool below ${input(a.cool_below_entity, a.cool_below, "°C")} °C</span>
        <span class="custom"><ha-icon icon="mdi:white-balance-sunny"></ha-icon> hot from ${input(a.hot_from_entity, a.hot_from, "°C")} °C</span>
      </div>
      <div class="chips">
        <span class="custom"><ha-icon icon="mdi:weather-windy"></ha-icon> ET cool below ${input(a.et_cool_below_entity, a.et_cool_below, "mm")} mm</span>
        <span class="custom">hot from ${input(a.et_hot_from_entity, a.et_hot_from, "mm")} mm</span>
        <span class="custom">today ${a.et_today != null ? `${fmt(a.et_today)} mm` : "–"}</span>
        <button class="chip done" data-close="1" aria-label="Close">✕</button>
      </div>
    </div>`;
  }

  _slotEditor(t, n) {
    const st = this._hass.states;
    const tm = n === 1 ? t.start_time : t.start_time_2;
    const whenId = t[`slot_${n}_when`];
    const shareId = t[`slot_${n}_share`];
    const when = st[whenId]?.state || "always";
    const share = parseFloat(st[shareId]?.state || "100");
    const whenChips = Object.entries(WHEN).map(([k, label]) =>
      `<button class="chip${when === k ? " on" : ""}" data-select="${esc(whenId)}" data-v="${k}">${label}</button>`).join("");
    const shareChips = SHARES.map((v) =>
      `<button class="chip${share === v ? " on" : ""}" data-set="${esc(shareId)}" data-v="${v}" data-keep="1">${v}%</button>`).join("");
    return `<div class="editor">
      <span class="elabel">Slot ${n}: runs on</span>
      <div class="chips">${whenChips}</div>
      <span class="elabel">Gives this much of the day-type amount</span>
      <div class="chips">${shareChips}
        <span class="custom"><input type="number" inputmode="numeric" min="5" max="100" step="5" data-input="${esc(shareId)}"
          placeholder="${esc(fmt(share))}" aria-label="Percent"/> %</span>
      </div>
      <div class="chips">
        <button class="chip" data-more="${esc(tm)}"><ha-icon icon="mdi:clock-outline"></ha-icon> Change time (${esc((st[tm]?.state || "").slice(0, 5))})</button>
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
            <span>Today</span><b><ha-icon icon="${a.day_type_by === "evapotranspiration" ? "mdi:weather-windy" : BAND[a.day_type]?.[1] || "mdi:thermometer"}"></ha-icon>${esc(BAND[a.day_type]?.[0] || "–")}${a.temp_max_today != null ? ` ${fmt(a.temp_max_today)}°` : ""}</b>
            ${a.et_today != null ? `<em>ET ${fmt(a.et_today)} mm${a.day_type_by === "evapotranspiration" ? " (dry/windy)" : ""}</em>` : ""}
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
    // Two daily slots: tick to turn a slot on/off, tap the time to change it.
    const slots = [[t.slot_1, t.start_time], [t.slot_2, t.start_time_2]].map(([sw, tm], i) => {
      const n = i + 1;
      const on = st[sw]?.state === "on";
      const time = (st[tm]?.state || "").slice(0, 5);
      const icon = parseInt(time, 10) < 12 ? "mdi:weather-sunset-up" : "mdi:weather-night";
      const when = st[t[`slot_${n}_when`]]?.state || "always";
      const share = parseFloat(st[t[`slot_${n}_share`]]?.state || "100");
      const badge = [when === "hot" ? "hot" : when === "warm_hot" ? "warm+" : "", share < 100 ? `${fmt(share)}%` : ""]
        .filter(Boolean).join(" ");
      const key = `__slot${n}:${t.id}`;
      return `<span class="slot${on ? " on" : ""}${this._edit === key ? " open" : ""}">
        <input type="checkbox" data-toggle="${esc(sw)}" ${on ? "checked" : ""} aria-label="Slot ${n} on"/>
        <button class="slottime" data-edit="${esc(key)}" title="Slot ${n}: time, which days, how much"><ha-icon icon="${icon}"></ha-icon>${esc(time || "–")}${badge ? `<span class="badge">${esc(badge)}</span>` : ""}</button>
      </span>`;
    }).join("");
    const editSlot = [1, 2].find((n) => this._edit === `__slot${n}:${t.id}`);
    // Days of the week: tap to toggle.
    const days = a.days || [];
    const dayChips = DAY_KEYS.map((d, i) =>
      `<button class="day${days.includes(d) ? " on" : ""}" data-day="${d}" data-tap="${esc(t.status)}" title="${d}">${DAY_LETTERS[i]}</button>`).join("");
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
    const todays = (a.slot_plan || []).filter((p) => p.runs_today);
    const plan = factor > 0 && todays.length
      ? ` · ${todays.map((p) => `${fmt(p.amount)} ${unit}`).join(" + ")} (${(BAND[a.day_type]?.[0] || "").toLowerCase()} day)`
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
      <div class="row ctl line">
        <span class="trio" title="Cool / warm / hot day amounts (${unit})">${amounts}<span class="unit">${unit}</span></span>
        <button class="val${this._edit === t.rain_threshold ? " open" : ""}" data-edit="${esc(t.rain_threshold)}" title="Rain threshold (past + next 24 h)">
          <ha-icon icon="mdi:weather-pouring"></ha-icon>${thrNum > 0 ? `${esc(fmt(thr))}<span class="u">mm</span>` : "off"}</button>
        ${slots}
      </div>
      <div class="row days" role="group" aria-label="Days">${dayChips}</div>
      ${editSlot ? this._slotEditor(t, editSlot) : ""}
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
    const el = ev.composedPath().find((n) => n.dataset && (n.dataset.press || n.dataset.set || n.dataset.edit || n.dataset.close || n.dataset.more || n.dataset.toggle || n.dataset.act || n.dataset.day || n.dataset.select));
    if (!el || !this._hass) return;
    const d = el.dataset;
    if (d.press) this._hass.callService("button", "press", { entity_id: d.press });
    else if (d.act === "refresh") this._hass.callService("waterha", "refresh_rain", {});
    else if (d.day) {
      const cur = this._hass.states[d.tap]?.attributes?.days || [];
      const next = cur.includes(d.day) ? cur.filter((x) => x !== d.day) : [...cur, d.day];
      this._hass.callService("waterha", "set_days", { entity_id: d.tap, days: DAY_KEYS.filter((x) => next.includes(x)) });
    } else if (d.select) {
      this._hass.callService("select", "select_option", { entity_id: d.select, option: d.v });
    }
    else if (d.toggle) {
      ev.preventDefault();
      const on = this._hass.states[d.toggle]?.state === "on";
      this._hass.callService("switch", on ? "turn_off" : "turn_on", { entity_id: d.toggle });
    } else if (d.set) {
      const keep = d.keep ? this._edit : null;
      this._setNumber(d.set, d.v);
      if (keep) { this._edit = keep; this._render(this._rainState(), this._taps || []); }
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
  .trio { display: inline-flex; align-items: center; gap: 3px; }
  .ctl.line { flex-wrap: nowrap; gap: 6px; overflow-x: auto; scrollbar-width: none; }
  .ctl.line::-webkit-scrollbar { display: none; }
  .ctl.line > * { flex: none; }
  .ctl.line .val { min-height: 30px; padding: 0 7px; gap: 2px; --mdc-icon-size: 16px; }
  .ctl.line .slot { min-height: 30px; padding: 0 6px 0 2px; --mdc-icon-size: 16px; }
  .ctl.line .slot input { width: 15px; height: 15px; margin: 0 2px; }
  .u { font-size: 0.8em; color: var(--secondary-text-color); margin-left: 1px; }
  /* Phones: tighter, unit labels and slot icons dropped so the values line still fits. */
  .tap { container-type: inline-size; }
  @container (max-width: 440px) {
    .ctl.line { gap: 4px; }
    .ctl.line .trio { gap: 2px; }
    .ctl.line .val, .ctl.line .val.band { padding: 0 5px; --mdc-icon-size: 15px; }
    .ctl.line .slot { padding: 0 5px 0 1px; }
    .ctl.line .slot ha-icon, .ctl.line .u, .ctl.line .unit { display: none; }
    .ctl.line .badge { margin-left: 1px; padding: 0 2px; font-size: 0.6em; }
    .ctl.line .slot input { margin: 0 1px; }
  }
  .slot { display: inline-flex; align-items: center; gap: 2px; min-height: 32px; padding: 0 8px 0 4px;
          border: 1px dashed var(--divider-color); border-radius: 16px; color: var(--secondary-text-color);
          --mdc-icon-size: 18px; }
  .slot.on { border-style: solid; color: var(--primary-text-color); }
  .slot input { margin: 0 2px; cursor: pointer; }
  .slottime { display: inline-flex; align-items: center; gap: 3px; }
  .slot.open { border-color: var(--primary-color); }
  .badge { font-size: 0.7em; margin-left: 3px; padding: 0 4px; border-radius: 6px;
           background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .days { margin-top: 6px; gap: 4px; flex-wrap: nowrap; }
  .day { width: 30px; height: 26px; border-radius: 13px; font-size: 0.85em; border: 1px solid var(--divider-color);
         color: var(--secondary-text-color); }
  .day.on { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
  .editor .chips + .elabel { display: block; margin-top: 8px; }
  .stat.today em { font-style: normal; font-size: 0.75em; color: var(--secondary-text-color); }
  .trio .unit { font-size: 0.8em; color: var(--secondary-text-color); }
  .val.band { padding: 0 6px; }
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


/* WaterHA history card: what ran, and when.
 *
 *   type: custom:waterha-history-card
 *   title: Watering history   # optional
 *   hours: 24                 # chart window, optional
 *   limit: 15                 # log rows shown before "Show more", optional
 */
const RUN_COLOR = {
  running: "var(--info-color, #039be5)",
  completed: "var(--info-color, #039be5)",
  max_time: "var(--warning-color, #ffa600)",
  no_flow: "var(--warning-color, #ffa600)",
  stopped: "var(--warning-color, #ffa600)",
  stopped_externally: "var(--warning-color, #ffa600)",
  interrupted_by_restart: "var(--error-color, #db4437)",
  failed: "var(--error-color, #db4437)",
  skipped_rain: "var(--success-color, #43a047)",
};
const RUN_TEXT = { ...RESULT, running: "watering now", completed: "done" };

function hm(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

class WaterHAHistoryCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._sig = null;
    this._more = false;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => {
        if (ev.composedPath().some((n) => n.dataset?.more)) {
          this._more = !this._more;
          this._render(this._logState());
        }
      });
    }
  }

  static getStubConfig() {
    return { title: "Watering history" };
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    const s = this._logState();
    // Redraw on new data, and at least each minute so the chart's "now" moves.
    const sig = `${s?.last_updated}|${Math.floor(Date.now() / 60000)}`;
    if (sig === this._sig) return;
    this._sig = sig;
    this._render(s);
  }

  _logState() {
    const states = this._hass.states;
    if (this._config.entity) return states[this._config.entity];
    return Object.values(states).find((s) => s.attributes && s.attributes.waterha === "log");
  }

  _render(s) {
    const title = this._config.title ?? "Watering history";
    if (!s) {
      this.shadowRoot.innerHTML = `${HSTYLE}<ha-card header="${esc(title)}"><div class="empty">
        No WaterHA watering log found. Update WaterHA to 0.4 or later.</div></ha-card>`;
      return;
    }
    const a = s.attributes;
    const runs = a.runs || [];
    const hours = this._config.hours || 24;
    const limit = this._config.limit || 15;
    const shown = this._more ? runs : runs.slice(0, limit);
    const totals = [
      `${s.state} run${s.state === "1" ? "" : "s"}`,
      a.minutes_24h ? `${fmt(a.minutes_24h)} min` : null,
      a.litres_24h != null ? `${fmt(a.litres_24h)} L` : null,
    ].filter(Boolean).join(" · ");
    this.shadowRoot.innerHTML = `${HSTYLE}
      <ha-card>
        <div class="head"><div class="title">${esc(title)}</div><div class="tot">Last ${hours} h: ${esc(totals)}</div></div>
        ${this._timeline(a.taps || [], runs, hours)}
        <div class="log">
          ${shown.length ? shown.map((r) => this._row(r)).join("") : `<div class="empty">Nothing has run yet.</div>`}
        </div>
        ${runs.length > limit ? `<button class="more" data-more="1">${this._more ? "Show fewer" : `Show all ${runs.length}`}</button>` : ""}
      </ha-card>`;
  }

  _timeline(taps, runs, hours) {
    const now = Date.now();
    const from = now - hours * 3600000;
    // One lane per tap; taps no longer configured still get a lane if they ran.
    const lanes = taps.map((t) => ({ id: t.id, name: t.name }));
    for (const r of runs) if (!lanes.some((l) => l.id === r.tap)) lanes.push({ id: r.tap, name: r.name || "?" });
    if (!lanes.length) return "";
    const L = 92, W = 480, lane = 22, top = 4;
    const H = top + lanes.length * lane;
    const x = (t) => L + ((t - from) / (now - from)) * (W - L);
    let ticks = "";
    const step = hours <= 24 ? 6 : 12;
    const first = new Date(from);
    first.setMinutes(0, 0, 0);
    for (let t = first.getTime() + 3600000; t < now; t += 3600000) {
      const d = new Date(t);
      if (d.getHours() % step) continue;
      ticks += `<line x1="${x(t)}" x2="${x(t)}" y1="${top}" y2="${H}" class="grid"/>
        <text x="${x(t)}" y="${H + 12}" class="lbl" text-anchor="middle">${d.getHours() === 0 ? d.toLocaleDateString([], { weekday: "short" }) : hm(d.toISOString())}</text>`;
    }
    const rows = lanes.map((l, i) => {
      const y = top + i * lane;
      const bars = runs.filter((r) => r.tap === l.id).map((r) => {
        const s = new Date(r.start).getTime();
        const e = r.end ? new Date(r.end).getTime() : now;
        if (e < from) return "";
        const color = RUN_COLOR[r.result] || "var(--secondary-text-color)";
        const tip = `${r.name || l.name}: ${when(r.start)}${r.end && r.end !== r.start ? `–${hm(r.end)}` : ""} · ${RUN_TEXT[r.result] || r.result}${r.minutes ? ` · ${fmt(r.minutes)} min` : ""}${r.litres != null ? ` · ${fmt(r.litres)} L` : ""}`;
        if (r.result === "skipped_rain" || r.minutes === null) {
          // A skip (or a run of unknown length): a marker, not a bar.
          return `<g><title>${esc(tip)}</title><path d="M${x(s)} ${y + 4} l4 7 l-4 7 l-4 -7 z" fill="${color}"/></g>`;
        }
        const x1 = x(Math.max(s, from));
        const w = Math.max(3, x(e) - x1);
        return `<rect x="${x1.toFixed(1)}" y="${y + 4}" width="${w.toFixed(1)}" height="${lane - 8}" rx="3"
          fill="${color}"${r.result === "running" ? ' class="live"' : ""}><title>${esc(tip)}</title></rect>`;
      }).join("");
      return `<text x="0" y="${y + lane / 2 + 4}" class="name">${esc(l.name)}</text>
        <line x1="${L}" x2="${W}" y1="${y + lane}" y2="${y + lane}" class="lane"/>${bars}`;
    }).join("");
    return `<svg class="tl" viewBox="0 0 ${W} ${H + 16}" role="img" aria-label="Watering in the last ${hours} hours">
      ${ticks}${rows}<line x1="${W - 0.5}" x2="${W - 0.5}" y1="${top}" y2="${H}" class="now"/>
    </svg>
    <div class="legend"><span style="--c:${RUN_COLOR.completed}">ran</span><span style="--c:${RUN_COLOR.stopped}">stopped early</span><span style="--c:${RUN_COLOR.failed}">failed</span><span class="dia" style="--c:${RUN_COLOR.skipped_rain}">skipped for rain</span></div>`;
  }

  _row(r) {
    const color = RUN_COLOR[r.result] || "var(--secondary-text-color)";
    const amount = r.result === "skipped_rain"
      ? (r.rain_mm != null ? `${fmt(r.rain_mm)} mm rain` : "")
      : [r.minutes != null ? `${fmt(r.minutes)} min` : null, r.litres != null ? `${fmt(r.litres)} L` : null].filter(Boolean).join(" · ");
    const detail = [
      r.reason === "schedule" ? (r.slot ? `slot ${r.slot}` : "scheduled") : r.reason === "manual" ? "manual" : r.reason,
      r.band ? `${r.band} day` : null,
      r.rain_factor != null && r.rain_factor > 0 && r.rain_factor < 1 ? `−${Math.round((1 - r.rain_factor) * 100)}% rain` : null,
      r.target && r.result !== "completed" && r.result !== "running" && r.result !== "skipped_rain" ? `of ${fmt(r.target)} ${r.mode === "litres" ? "L" : "min"}` : null,
    ].filter(Boolean).join(" · ");
    return `<div class="row">
      <span class="dot" style="background:${color}"></span>
      <span class="when">${esc(when(r.start))}${r.end && r.end !== r.start ? `–${esc(hm(r.end))}` : ""}</span>
      <span class="who">${esc(r.name || "")}</span>
      <span class="amt">${esc(amount)}</span>
      <span class="res" style="color:${color}">${esc(RUN_TEXT[r.result] || r.result)}</span>
      ${detail ? `<span class="det">${esc(detail)}</span>` : ""}
    </div>`;
  }
}

const HSTYLE = `<style>
  ha-card { padding: 12px 16px 8px; }
  .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; margin-bottom: 6px; }
  .title { font-size: 1.25em; font-weight: 500; flex: 1; }
  .tot { font-size: 0.85em; color: var(--secondary-text-color); }
  .tl { width: 100%; display: block; }
  .tl .name { font-size: 11px; fill: var(--primary-text-color); }
  .tl .lbl { font-size: 9px; fill: var(--secondary-text-color); }
  .tl .grid { stroke: var(--divider-color); stroke-dasharray: 2 3; }
  .tl .lane { stroke: var(--divider-color); }
  .tl .now { stroke: var(--primary-text-color); stroke-width: 1; }
  .tl .live { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.55; } }
  .legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 0.75em; color: var(--secondary-text-color); margin: 2px 0 8px; }
  .legend span::before { content: ""; display: inline-block; width: 10px; height: 8px; border-radius: 2px;
    background: var(--c); margin-right: 4px; }
  .legend span.dia::before { width: 7px; height: 7px; transform: rotate(45deg); border-radius: 1px; }
  .log { border-top: 1px solid var(--divider-color); }
  .row { display: grid; grid-template-columns: 10px minmax(110px, auto) 1fr auto auto; column-gap: 8px;
         align-items: baseline; padding: 6px 0; border-bottom: 1px solid var(--divider-color); font-size: 0.9em; }
  .row:last-child { border-bottom: 0; }
  .dot { width: 8px; height: 8px; border-radius: 50%; align-self: center; }
  .when { color: var(--secondary-text-color); white-space: nowrap; }
  .who { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .amt { white-space: nowrap; }
  .res { white-space: nowrap; font-size: 0.9em; }
  .det { grid-column: 3 / -1; font-size: 0.8em; color: var(--secondary-text-color); }
  .more { font: inherit; color: var(--primary-color); background: none; border: 0; cursor: pointer; padding: 8px 0 4px; }
  .empty { padding: 12px 0; color: var(--secondary-text-color); }
  @container (max-width: 400px) { .row { grid-template-columns: 10px auto 1fr; } .amt, .res { grid-column: 3; } }
  ha-card { container-type: inline-size; }
</style>`;

if (!customElements.get("waterha-history-card")) {
  customElements.define("waterha-history-card", WaterHAHistoryCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "waterha-history-card",
    name: "WaterHA history",
    description: "What watered and when: a 24 h timeline and a log.",
  });
}


/* WaterHA cost card: what watering costs.
 *
 *   type: custom:waterha-cost-card
 *   title: Water cost   # optional
 *   days: 30            # chart window (max 31), optional
 */
const TAP_COLORS = ["#039be5", "#43a047", "#fb8c00", "#8e24aa", "#e53935", "#00897b", "#6d4c41", "#3949ab"];

class WaterHACostCard extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._sig = null;
    this._editPrice = false;
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this.shadowRoot.addEventListener("click", (ev) => {
        if (ev.composedPath().some((n) => n.dataset?.price)) {
          this._editPrice = !this._editPrice;
          this._render(this._costState(), true);
          if (this._editPrice) this.shadowRoot.querySelector("input")?.focus();
        }
      });
      this.shadowRoot.addEventListener("change", (ev) => {
        const v = parseFloat(ev.target.value);
        const id = this._priceEntity();
        if (!isNaN(v) && v >= 0 && id) this._hass.callService("number", "set_value", { entity_id: id, value: v });
        this._editPrice = false;
      });
      this.shadowRoot.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") ev.target.blur?.();
        if (ev.key === "Escape") { this._editPrice = false; this._render(this._costState(), true); }
      });
    }
  }

  static getStubConfig() {
    return { title: "Water cost" };
  }

  getCardSize() {
    return 6;
  }

  set hass(hass) {
    this._hass = hass;
    const s = this._costState();
    const sig = `${s?.last_updated}|${s?.state}`;
    if (sig === this._sig) return;
    this._sig = sig;
    if (this.shadowRoot.activeElement?.tagName === "INPUT") return; // never redraw under typing
    this._render(s);
  }

  _costState() {
    const states = this._hass.states;
    if (this._config.entity) return states[this._config.entity];
    return Object.values(states).find((s) => s.attributes && s.attributes.waterha === "cost");
  }

  _priceEntity() {
    const reg = this._hass.entities || {};
    return Object.keys(this._hass.states).find((id) => id.startsWith("number.") && id.endsWith("_water_price")
      && (!reg[id] || reg[id].platform === "waterha"));
  }

  _money(v) {
    const cur = this._costState()?.attributes?.currency || "NZD";
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, minimumFractionDigits: 2 }).format(v || 0);
    } catch (e) {
      return `$${(v || 0).toFixed(2)}`;
    }
  }

  _litres(v) {
    v = v || 0;
    return v >= 10000 ? `${(v / 1000).toFixed(1)} m³` : `${Math.round(v).toLocaleString()} L`;
  }

  _render(s) {
    const title = this._config.title ?? "Water cost";
    if (!s) {
      this.shadowRoot.innerHTML = `${CSTYLE}<ha-card header="${esc(title)}"><div class="empty">
        No WaterHA cost data found. Update WaterHA to 0.5 or later.</div></ha-card>`;
      return;
    }
    const a = s.attributes;
    const tiles = [
      ["Today", a.today],
      ["Last 7 days", a.week],
      ["This month", a.month],
      ["Month (projected)", a.projected_month],
    ].map(([label, t]) => `<div class="tile"><span>${label}</span><b>${this._money(t?.cost)}</b><em>${this._litres(t?.litres)}</em></div>`).join("");
    const tapNames = [...new Set((a.per_tap_30d || []).map((t) => t.name))];
    const color = (name) => TAP_COLORS[Math.max(0, tapNames.indexOf(name)) % TAP_COLORS.length];
    const perTap = (a.per_tap_30d || []).map((t) => {
      const share = a.days30?.litres ? (100 * t.litres) / a.days30.litres : 0;
      return `<div class="tap"><span class="sw" style="background:${color(t.name)}"></span><span class="n">${esc(t.name)}</span>
        <span class="bar"><span style="width:${share.toFixed(0)}%;background:${color(t.name)}"></span></span>
        <span class="l">${this._litres(t.litres)}</span><span class="c">${this._money(t.cost)}</span></div>`;
    }).join("");
    const price = this._editPrice
      ? `<span class="pedit">${esc(a.currency || "")} <input type="number" inputmode="decimal" step="0.01" min="0" placeholder="${esc(a.price)}" aria-label="Price per 1000 litres"/> per 1000 L</span>`
      : `<button class="price" data-price="1" title="Change the water price">${this._money(a.price)} per 1000 L</button>`;
    const notes = [];
    if (a.since) notes.push(`Measured since ${new Date(a.since).toLocaleDateString([], { day: "numeric", month: "short" })}`);
    if ((a.not_measured || []).length) notes.push(`Not counted (no flow sensor): ${a.not_measured.join(", ")}`);
    this.shadowRoot.innerHTML = `${CSTYLE}
      <ha-card>
        <div class="head"><div class="title">${esc(title)}</div>${price}</div>
        <div class="tiles">${tiles}</div>
        ${this._chart(a.series || [], color)}
        ${perTap ? `<div class="sub">Last 30 days by tap · ${this._litres(a.days30?.litres)} · ${this._money(a.days30?.cost)}</div><div class="taps">${perTap}</div>` : `<div class="empty">No measured watering yet. Costs appear after the first run of a tap with a flow sensor.</div>`}
        ${notes.length ? `<div class="note">${esc(notes.join(" · "))}</div>` : ""}
      </ha-card>`;
  }

  _chart(series, color) {
    const days = Math.min(this._config.days || 30, series.length);
    const data = series.slice(-days);
    if (!data.length) return "";
    const W = 480, H = 96, pad = 14;
    const max = Math.max(0.01, ...data.map((d) => d.cost));
    const price = this._costState().attributes.price || 0;
    const bw = W / data.length;
    const bars = data.map((d, i) => {
      let y = H;
      const parts = Object.entries(d.taps || {}).map(([name, litres]) => {
        const c = (litres * price) / 1000;
        const h = (c / max) * (H - pad);
        y -= h;
        return `<rect x="${(i * bw + 1).toFixed(1)}" y="${y.toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${h.toFixed(1)}" fill="${color(name)}"/>`;
      }).join("");
      const day = new Date(d.date + "T12:00:00");
      const label = day.toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });
      return `<g><title>${esc(label)}: ${this._money(d.cost)} · ${this._litres(d.litres)}</title>
        <rect x="${i * bw}" y="0" width="${bw}" height="${H}" fill="transparent"/>${parts}</g>`;
    }).join("");
    const first = new Date(data[0].date + "T12:00:00").toLocaleDateString([], { day: "numeric", month: "short" });
    return `<svg class="chart" viewBox="0 0 ${W} ${H + 12}" preserveAspectRatio="none" role="img" aria-label="Daily water cost">
      <line x1="0" x2="${W}" y1="${H}" y2="${H}" class="axis"/>${bars}
      <text x="0" y="${H + 11}" class="lbl">${esc(first)}</text><text x="${W}" y="${H + 11}" class="lbl" text-anchor="end">today</text>
      <text x="${W}" y="9" class="lbl" text-anchor="end">max ${esc(this._money(max))}/day</text>
    </svg>`;
  }
}

const CSTYLE = `<style>
  ha-card { padding: 12px 16px 10px; container-type: inline-size; }
  .head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
  .title { font-size: 1.25em; font-weight: 500; flex: 1; }
  .price { font: inherit; font-size: 0.85em; color: var(--primary-color); background: none; border: 0; cursor: pointer; padding: 0; }
  .pedit { font-size: 0.85em; color: var(--secondary-text-color); }
  .pedit input { width: 70px; font: inherit; padding: 2px 6px; border-radius: 12px; border: 1px solid var(--divider-color);
    background: var(--card-background-color, transparent); color: var(--primary-text-color); }
  .tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0 6px; }
  @container (max-width: 420px) { .tiles { grid-template-columns: repeat(2, 1fr); } }
  .tile { display: flex; flex-direction: column; }
  .tile span { font-size: 0.8em; color: var(--secondary-text-color); }
  .tile b { font-size: 1.3em; font-weight: 500; }
  .tile em { font-style: normal; font-size: 0.8em; color: var(--secondary-text-color); }
  .chart { width: 100%; height: 108px; display: block; margin: 4px 0; }
  .chart .axis { stroke: var(--divider-color); }
  .chart .lbl { font-size: 9px; fill: var(--secondary-text-color); }
  .sub { font-size: 0.8em; color: var(--secondary-text-color); margin: 8px 0 4px; }
  .tap { display: grid; grid-template-columns: 10px minmax(70px, auto) 1fr auto auto; gap: 8px; align-items: center;
         padding: 3px 0; font-size: 0.9em; }
  .sw { width: 10px; height: 10px; border-radius: 2px; }
  .bar { height: 6px; background: var(--divider-color); border-radius: 3px; overflow: hidden; }
  .bar span { display: block; height: 100%; }
  .l { color: var(--secondary-text-color); white-space: nowrap; }
  .c { font-weight: 500; white-space: nowrap; }
  .note { font-size: 0.75em; color: var(--secondary-text-color); margin-top: 8px; }
  .empty { padding: 12px 0; color: var(--secondary-text-color); }
</style>`;

if (!customElements.get("waterha-cost-card")) {
  customElements.define("waterha-cost-card", WaterHACostCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "waterha-cost-card",
    name: "WaterHA cost",
    description: "What watering costs: today, week, month, a 30-day chart and per tap.",
  });
}
