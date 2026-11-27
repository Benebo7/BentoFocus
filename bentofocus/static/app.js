const MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const pad = (n) => String(n).padStart(2, "0");
const parseDay = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
const isoDay = (dt) => `${dt.getFullYear()}-${pad(dt.getMonth() + 1)}-${pad(dt.getDate())}`;

function app() {
  return {
    view: "hoje",
    s: null,        // estado do painel
    c: null,        // status do coletor
    f: null,        // finanças
    fmonth: null,
    now: Date.now(),
    error: "",
    habitDay: "hoje",
    form: { intention: "", minutes: 25, cycles: 1, allow_youtube: false, spotify: false },
    logf: { open: false, minutes: 60, end: "", intention: "", err: "" },  // foco feito sem o timer
    summary: { open: false, loading: false, copied: false, data: null },
    tx: { amount: "", description: "", category: "", kind: "out" },
    fx: { name: "", amount: "" },
    habitDefs: [
      { key: "leitura", name: "Leitura", goal: 7 },
      { key: "academia", name: "Academia", goal: 3 },
      { key: "faculdade", name: "Faculdade" },
    ],

    async init() {
      try { this.form.spotify = localStorage.getItem("bf.spotify") === "1"; } catch {}
      if (location.hash === "#financas") { this.view = "financas"; this.loadFinance(); }
      await this.load();
      const params = new URLSearchParams(location.search);
      if (params.has("resumo") || location.hash === "#resumo") {
        const qDay = params.get("resumo");
        this.openSummary(qDay && /^\d{4}-\d{2}-\d{2}$/.test(qDay) ? qDay : undefined);
      }
      this.loadCollector();
      let tick = 0;
      setInterval(() => {
        // Escondido (minimizado/coberto): não redesenha. Sem bloco nem pausa:
        // 1x por segundo basta. Com timer correndo: 4x, para o segundo virar no tempo certo.
        if (document.hidden) return;
        const live = this.s?.current || this.s?.plan?.next_at || this.s?.break_until;
        if (!live && ++tick % 4) return;
        this.now = Date.now();
        this.syncTitle();
        const edge = this.s?.current ? this.s.ends_at : this.s?.plan?.next_at;
        if (edge && this.msUntil(edge) === 0 && !this._ending) {
          this._ending = true;  // o servidor vira o estado em até 1s; busca o novo
          setTimeout(() => this.load().then(() => (this._ending = false)), 1200);
        }
      }, 250);
      const poll = async () => {
        if (!document.hidden) await this.load();
        setTimeout(poll, this.s?.current || this.s?.plan ? 2000 : 10000);
      };
      setTimeout(poll, 2000);
      setInterval(() => !document.hidden && this.loadCollector(), 10000);
      this.$nextTick(() => {
        if (!this.summary.open) document.querySelector(".intent")?.focus();
      });
    },

    selectView(name) {
      this.view = name;
      if (name === "financas") {
        location.hash = "#financas";
        this.loadFinance();
      } else if (location.hash) {
        history.replaceState(null, "", location.pathname + location.search);
      }
    },
    onTabKey(e) {
      const tabs = [...e.currentTarget.querySelectorAll('[role="tab"]')];
      const idx = tabs.indexOf(e.target);
      if (idx < 0) return;
      let next = -1;
      if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
      else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = tabs.length - 1;
      if (next >= 0) {
        e.preventDefault();
        tabs[next].focus();
      }
    },

    // --- rede -------------------------------------------------------------
    async load() {
      try {
        const r = await fetch("/api/state");
        if (r.ok) this.s = await r.json();
      } catch { this.flash("servidor do BentoFocus fora do ar"); }
    },
    async loadCollector() {
      try { this.c = await (await fetch("/api/collector")).json(); } catch { this.c = null; }
    },
    async req(method, url, body) {
      const r = await fetch(url, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        this.flash(typeof e.detail === "string" ? e.detail : "algo deu errado");
        return null;
      }
      return r.json();
    },
    async post(url, body) {
      const data = await this.req("POST", url, body);
      if (data) this.s = data;
    },
    flash(msg) {
      this.error = msg;
      clearTimeout(this._err);
      this._err = setTimeout(() => (this.error = ""), 3500);
    },

    // --- foco ---------------------------------------------------------------
    toggleSpotify() {
      this.form.spotify = !this.form.spotify;
      try { localStorage.setItem("bf.spotify", this.form.spotify ? "1" : "0"); } catch {}
    },
    async start() {
      await this.post("/api/focus/start", this.form);
      if (this.s?.current) this.form.intention = "";
    },
    // Foco feito sem o timer: minutos e a hora em que terminou (padrão: agora).
    openLog() {
      const d = new Date();
      this.logf = { open: true, minutes: 60, end: `${pad(d.getHours())}:${pad(d.getMinutes())}`, intention: "", err: "" };
    },
    // A hora vale dentro do "hoje" do app (o dia vira às 4h): de madrugada,
    // "23:30" é a noite que passou e "03:00" é daqui a pouco, nunca ontem.
    logEnd() {
      const [h, m] = (this.logf.end || "").split(":").map(Number);
      const now = new Date();
      if (Number.isNaN(h) || Number.isNaN(m)) return now;
      const end = new Date(now);
      if (now.getHours() < 4) end.setDate(end.getDate() - 1);
      end.setHours(h, m, 0, 0);
      if (h < 4) end.setDate(end.getDate() + 1);
      return end;
    },
    logRange() {
      const min = +this.logf.minutes;
      if (!(min >= 5)) return "mínimo 5 min";
      const end = this.logEnd(), start = new Date(end.getTime() - min * 60e3);
      const hm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
      return `das ${hm(start)} às ${hm(end)}`;
    },
    // Aviso antes de salvar: o intervalo bate com um bloco do histórico (o
    // servidor recusa, para o mesmo tempo não contar duas vezes).
    logClash() {
      const min = +this.logf.minutes;
      if (!(min >= 5)) return "";
      const end = this.logEnd(), start = new Date(end.getTime() - min * 60e3);
      if (end - Date.now() > 60e3) return "esse horário ainda não chegou";
      const o = (this.s?.recent || []).find((r) => r.ended_at && new Date(r.started_at) < end && new Date(r.ended_at) > start);
      return o ? `bate com o registro das ${this.hhmm(o.started_at)} às ${this.hhmm(o.ended_at)}: termine mais tarde ou use menos minutos` : "";
    },
    async saveLog() {
      const end = this.logEnd();
      this.logf.err = "";
      const r = await fetch("/api/focus/log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          minutes: +this.logf.minutes,
          intention: this.logf.intention,
          ended_at: `${isoDay(end)}T${pad(end.getHours())}:${pad(end.getMinutes())}:00`,
        }),
      }).catch(() => null);
      if (r?.ok) { this.s = await r.json(); this.logf.open = false; return; }
      // O erro fica no formulário, onde você está olhando (o aviso de baixo some em 3 s).
      const e = r ? await r.json().catch(() => ({})) : {};
      this.logf.err = typeof e.detail === "string" ? e.detail : r?.status === 422 ? "confira os minutos (5 a 600)" : "não deu para registrar";
    },
    async deleteLog(id) {
      const data = await this.req("DELETE", `/api/focus/${id}`);
      if (data) this.s = data;
    },
    msUntil(iso) {
      return iso ? Math.max(0, new Date(iso).getTime() - this.now) : 0;
    },
    countdown(iso) {
      const t = Math.ceil(this.msUntil(iso) / 1000);
      return { mm: pad(Math.floor(t / 60)), ss: pad(t % 60) };
    },
    progress() {
      const total = this.s.current.planned_min * 60000;
      return Math.min(100, (1 - this.msUntil(this.s.ends_at) / total) * 100);
    },
    breakProgress() {
      const start = new Date(this.s?.last?.ended_at).getTime(), end = new Date(this.s?.plan?.next_at).getTime();
      return end > start ? Math.min(100, ((this.now - start) / (end - start)) * 100) : 0;
    },
    inBreak() {
      return !this.s?.current && this.msUntil(this.s?.break_until) > 0;
    },
    syncTitle() {
      let t = "BentoFocus";
      if (this.s?.current) {
        const c = this.countdown(this.s.ends_at);
        t = `${c.mm}:${c.ss} · ${this.s.current.intention || "foco"}`;
      } else if (this.s?.plan?.next_at) {
        const c = this.countdown(this.s.plan.next_at);
        t = `pausa ${c.mm}:${c.ss}`;
      }
      if (document.title !== t) document.title = t;
    },

    // --- hábitos -------------------------------------------------------------
    yesterday() {
      const d = parseDay(this.s.today);
      d.setDate(d.getDate() - 1);
      return isoDay(d);
    },
    habitOn(key) {
      const list = this.habitDay === "hoje" ? this.s?.habits_today : this.s?.habits_yesterday;
      return (list || []).includes(key);
    },
    toggleHabit(habit, day) {
      day = day || (this.habitDay === "ontem" ? this.yesterday() : null);
      const listKey = this.habitDay === "hoje" ? "habits_today" : "habits_yesterday";
      if (this.s && Array.isArray(this.s[listKey])) {
        const idx = this.s[listKey].indexOf(habit);
        if (idx >= 0) this.s[listKey].splice(idx, 1);
        else this.s[listKey].push(habit);
      }
      return this.post("/api/habits/toggle", { habit, day });
    },

    // --- heatmaps ------------------------------------------------------------
    months(grid) {
      if (!grid?.length) return [];
      const out = [];
      let prev = -1, lastCol = -9;
      for (let i = 0; i < grid.length; i += 7) {
        const m = parseDay(grid[i].d).getMonth();
        const col = i / 7;
        if (m !== prev && col - lastCol >= 3) { out.push({ col, label: MONTHS[m] }); lastCol = col; }
        prev = m;
      }
      return out;
    },
    cols(grid) { return `--cols:${Math.ceil((grid?.length || 7) / 7)}`; },
    blockedNow() {
      const list = this.s?.blocked_list || [];
      return this.s?.current?.allow_youtube ? list.filter((b) => b !== "YouTube") : list;
    },
    breakFor(m) { return m >= 45 ? 15 : m >= 35 ? 10 : 5; },
    habitCount(h) {
      const n = h.goal ? this.s?.week?.[h.key] : (this.s?.week?.faculdade || []).filter(Boolean).length;
      return h.goal ? `${n}/${h.goal}` : `${n} ${n === 1 ? "dia" : "dias"}`;
    },
    insight() {
      const { minutes: m, cycles: n } = this.form;
      if (n > 1) {
        const total = m * n + this.breakFor(m) * (n - 1);
        return `<span><b>${n} × ${m} min</b> com pausa de ${this.breakFor(m)} → ${this.fmtMin(total)} no total</span>`
          + `<span>os blocos seguintes começam sozinhos</span>`;
      }
      const w = this.s?.this_week_min || 0, p = this.s?.prev_week_same_point_min || 0;
      const blocked = (this.s?.blocked_list || []).join(", ");
      if (!w && !p) return `<span>durante o bloco ficam bloqueados: ${blocked}</span>`;
      let cmp = "";
      if (p > 0) {
        const d = w - p;
        cmp = d >= 0 ? `<span class="up">+${this.fmtMin(d)} vs. semana passada</span>` : `<span class="down">${this.fmtMin(-d)} abaixo da semana passada</span>`;
      }
      return `<span><b>${this.fmtMin(w)}</b> de foco nesta semana</span>${cmp}`;
    },
    pct(v, max) { return v && max ? Math.max(3, (v / max) * 100) : 0; },
    weeklyMax() { return Math.max(60, ...(this.s?.weekly || []).map((w) => w.m)); },
    // Escala com folga de 20% em cima: o número sobre a barra mais alta cabe
    // sem encolher a barra (antes ela ficava menor que a vizinha e que a média).
    weeklyScale() { return this.weeklyMax() * 1.2; },
    hoursMax() { return Math.max(1, ...(this.s?.hours || [])); },
    distMax() { return Math.max(30, ...(this.s?.distraction_days || []).map((d) => d.n + d.c)); },
    distScale() { return this.distMax() * 1.2; },
    inBest(h) { const b = this.s?.best_hours; return !!b && h >= b[0] && h < b[1]; },
    focusLvl(m) { return !m ? "" : m < 30 ? "f1" : m < 90 ? "f2" : m < 180 ? "f3" : "f4"; },
    gymLvl(n) { return !n ? "" : n === 1 ? "h2c" : n === 2 ? "h3" : "h4"; },
    todayIdx() { return this.s ? (parseDay(this.s.today).getDay() + 6) % 7 : -1; },
    weekBar(m) {
      const max = Math.max(60, ...(this.s?.week?.focus_min || [0]));
      return m ? Math.max(4, (m / max) * 100) : 3;
    },
    pcTotal() { const p = this.s?.pc || {}; return (p.active || 0) + (p.distraction || 0) + (p.cheap || 0) + (p.idle || 0); },

    // --- resumo diário ---------------------------------------------------------
    async openSummary(day) {
      const targetDay = day || this.s?.today;
      clearTimeout(this._copyTimer);
      this.summary = { open: true, loading: true, copied: false, data: null };
      this.$nextTick(() => this.$refs.summaryClose?.focus());
      const q = targetDay ? `?day=${encodeURIComponent(targetDay)}` : "";
      let data = null;
      try {
        data = await this.req("GET", `/api/export/daily${q}`);
      } catch {
        this.flash("não deu para carregar o resumo");
      }
      if (!this.summary.open) return;
      this.summary.loading = false;
      if (!data) {
        this.summary.open = false;
        return;
      }
      this.summary.data = data;
    },
    closeSummary() {
      this.summary.open = false;
      this.summary.copied = false;
      clearTimeout(this._copyTimer);
      if (location.search.includes("resumo") || location.hash === "#resumo") {
        const params = new URLSearchParams(location.search);
        params.delete("resumo");
        const qs = params.toString();
        const hash = location.hash === "#resumo" ? "" : location.hash;
        history.replaceState(null, "", location.pathname + (qs ? `?${qs}` : "") + hash);
      }
    },
    async copySummary() {
      const md = this.summary.data?.markdown;
      if (!md) return;
      let ok = false;
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(md);
          ok = true;
        } catch {}
      }
      if (!ok) {
        const ta = document.createElement("textarea");
        ta.value = md;
        ta.setAttribute("readonly", "");
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        try { ok = document.execCommand("copy"); } catch {}
        ta.remove();
      }
      if (!ok) {
        this.flash("não deu para copiar");
        return;
      }
      this.summary.copied = true;
      clearTimeout(this._copyTimer);
      this._copyTimer = setTimeout(() => (this.summary.copied = false), 2000);
    },
    summaryDownloadUrl() {
      const day = this.summary.data?.day || this.s?.today;
      return day
        ? `/api/export/daily?day=${encodeURIComponent(day)}&format=markdown`
        : "/api/export/daily?format=markdown";
    },
    summaryDateTitle() {
      const day = this.summary.data?.day || this.s?.today;
      if (!day) return "";
      return parseDay(day).toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    },
    summaryHabitsLabel() {
      const list = this.summary.data?.habits || [];
      return list.length ? list.join(", ") : "nenhum";
    },

    // --- coletor ---------------------------------------------------------------
    collectorOk() { return !this.c || (this.c.running && this.c.tools?.xdotool && this.c.tools?.xprop); },
    collectorTitle() {
      if (!this.c) return "";
      const idle = this.c.idle_supported ? `inatividade: ${Math.round(this.c.idle_s)}s` : "inatividade: indisponível";
      return `${this.c.wm_class || "?"} · ${this.c.category || "?"} · ${idle}`;
    },

    // --- formatação --------------------------------------------------------------
    dateTitle() {
      if (!this.s) return "";
      return parseDay(this.s.today).toLocaleDateString("pt-BR", { weekday: "long", day: "numeric", month: "long" });
    },
    fmtMin(m) {
      m = Math.round(m || 0);
      if (m < 60) return `${m}m`;
      return `${Math.floor(m / 60)}h ${pad(m % 60)}m`;
    },
    bigMin(m) {
      m = Math.round(m || 0);
      if (m < 60) return `${m}<small>m</small>`;
      return `${Math.floor(m / 60)}<small>h</small> ${pad(m % 60)}<small>m</small>`;
    },
    fmtShort(m) { return m < 60 ? `${m}m` : `${(m / 60).toFixed(1).replace(".", ",")}h`; },
    fmtDate(iso) { const d = parseDay(iso.slice(0, 10)); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; },
    hhmm(iso) { const d = new Date(iso); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; },
    when(iso) {
      const d = new Date(iso);
      const sameDay = isoDay(new Date(d.getTime() - 4 * 3600e3)) === this.s.today;
      return sameDay ? this.hhmm(iso) : `${d.getDate()} ${MONTHS[d.getMonth()]}`;
    },

    // --- finanças -----------------------------------------------------------------
    get categories() {
      const base = ["Comida", "Transporte", "Lazer", "Assinaturas", "Estudo", "Saúde", "Outros"];
      const seen = (this.f?.transactions || []).map((t) => t.category).filter(Boolean);
      return [...new Set([...base, ...seen])];
    },
    async loadFinance() {
      const q = this.fmonth ? `?month=${this.fmonth}` : "";
      this.f = await this.req("GET", `/api/finance${q}`);
      this.fmonth = this.f?.month;
    },
    shiftMonth(delta) {
      const [y, m] = this.fmonth.split("-").map(Number);
      const d = new Date(y, m - 1 + delta, 1);
      this.fmonth = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
      this.loadFinance();
    },
    monthTitle() {
      if (!this.fmonth) return "";
      const [y, m] = this.fmonth.split("-").map(Number);
      return new Date(y, m - 1, 1).toLocaleDateString("pt-BR", { month: "long", year: "numeric" });
    },
    cents(v) {
      // "1.234,56" e "12,50" (pt-BR) ou "12.50": com vírgula, ponto é milhar.
      const s = String(v).trim();
      const n = Number(s.includes(",") ? s.replace(/\./g, "").replace(",", ".") : s);
      return Number.isFinite(n) ? Math.round(n * 100) : 0;
    },
    brl(c) { return ((c || 0) / 100).toLocaleString("pt-BR", { style: "currency", currency: "BRL" }); },
    async addTx() {
      const amount_cents = this.cents(this.tx.amount);
      if (amount_cents <= 0) return this.flash("valor inválido");
      const data = await this.req("POST", "/api/finance/tx", { ...this.tx, amount_cents });
      if (data) { this.tx = { ...this.tx, amount: "", description: "" }; await this.loadFinance(); }
    },
    async addFixed() {
      const amount_cents = this.cents(this.fx.amount);
      if (amount_cents <= 0) return this.flash("valor inválido");
      const data = await this.req("POST", "/api/finance/fixed", { name: this.fx.name, amount_cents });
      if (data) { this.fx = { name: "", amount: "" }; await this.loadFinance(); }
    },
    async del(url) {
      if (await this.req("DELETE", url)) await this.loadFinance();
    },
  };
}
