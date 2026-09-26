"use client";
import { useEffect, useMemo, useRef, useState } from "react";

const SLOTS = 8; // categorical slots, fixed order by version number; beyond 8 -> neutral
const colorOf = (v) => (v && v.version <= SLOTS ? `var(--s${v.version})` : "var(--text-muted)");
const fmt = (x, d = 1) => (x == null ? "–" : Number(x).toFixed(d));

export default function Page() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState(null);
  useEffect(() => {
    let alive = true;
    const load = () => fetch("/api/data", { cache: "no-store" }).then((r) => r.json())
      .then((j) => { if (!alive) return; j.error ? setErr(j.error) : (setData(j), setErr(null)); })
      .catch((e) => alive && setErr(String(e)));
    load();
    const t = setInterval(load, 10000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!data) return <main><h1>Self-Evolving Chip-Design Harness</h1><p className={err ? "err" : "sub"}>{err || "Loading from MongoDB Atlas…"}</p></main>;
  const vmap = Object.fromEntries(data.versions.map((v) => [v._id, v]));
  const trials = data.trials;
  const valid = trials.filter((t) => t.score?.valid);
  const base = trials.find((t) => t._id === "t-h1-00");
  const best = valid.reduce((a, t) => (!a || t.score.fmax_mhz > a.score.fmax_mhz ? t : a), null);
  const gain = base && best ? (100 * (best.score.fmax_mhz - base.score.fmax_mhz)) / base.score.fmax_mhz : null;
  const kept = data.versions.filter((v) => v.status === "kept" && v.created_by !== "seed").length;
  const retired = data.versions.filter((v) => v.status === "retired").length;

  return (
    <main>
      <h1>Self-Evolving Chip-Design Harness</h1>
      <p className="sub">
        Agents redesign a 4×4 INT4 MAC array for maximum clock frequency. Every candidate is verified by a testbench and
        scored by full place-and-route (OpenROAD, Nangate45). When progress plateaus, an evolution agent rewrites the
        harness itself — prompts, tools, context, filters. State, trials, lessons and checkpoints live in MongoDB Atlas.
        {err && <span className="err"> (refresh failed: {err})</span>}
      </p>

      <div className="tiles">
        <Tile label="Baseline fmax" value={`${fmt(base?.score?.fmax_mhz)} MHz`} note="unmodified RTL, 1 GHz target" />
        <Tile label="Best fmax" value={`${fmt(best?.score?.fmax_mhz)} MHz`} note={best ? `${best._id} · ${best.harness_version}` : ""} />
        <Tile label="Improvement" value={gain == null ? "–" : `${gain >= 0 ? "+" : ""}${fmt(gain)}%`} note="best vs baseline" />
        <Tile label="Harness versions" value={data.versions.length} note={`${kept} kept · ${retired} retired`} />
        <Tile label="Trials" value={trials.length} note={`${valid.length} valid · ${trials.length - valid.length} rejected`} />
      </div>

      <div className="card">
        <h2>fmax per trial, colored by harness version</h2>
        <Chart trials={trials} versions={data.versions} vmap={vmap} />
      </div>

      <div className="card">
        <h2>Harness evolution</h2>
        <div className="versions">
          {[...data.versions].reverse().map((v) => <Version key={v._id} v={v} />)}
        </div>
      </div>

      <div className="card">
        <h2>Latest trials</h2>
        <div className="tablewrap"><table>
          <thead><tr><th>Trial</th><th>Harness</th><th>Fix family</th><th>Testbench</th><th>fmax MHz</th><th>WNS ns</th><th>DRC</th><th>Result</th><th>Goal</th></tr></thead>
          <tbody>
            {[...trials].reverse().slice(0, 50).map((t) => (
              <tr key={t._id}>
                <td>{t._id}</td><td>{t.harness_version}</td><td>{t.diagnosis?.fix_family || "–"}</td>
                <td>{t.stages?.testbench?.status || "–"}</td><td>{fmt(t.score?.fmax_mhz)}</td>
                <td>{fmt(t.stages?.tier2?.wns_ns, 3)}</td><td>{t.stages?.tier2?.drc_count ?? "–"}</td>
                <td>{t.score?.valid ? "valid" : `rejected: ${t.score?.reject_reason}`}</td>
                <td className="goal">{t.goal}</td>
              </tr>))}
          </tbody>
        </table></div>
      </div>
      <p className="sub" style={{ fontSize: 12 }}>Live from Atlas · refreshed {new Date(data.at).toLocaleTimeString()}</p>
    </main>
  );
}

function Tile({ label, value, note }) {
  return <div className="card tile" style={{ marginBottom: 0 }}><div className="label">{label}</div><div className="value">{value}</div><div className="note">{note}</div></div>;
}

function Version({ v }) {
  const cls = v.status === "kept" ? "kept" : v.status === "retired" ? "retired" : "";
  return (
    <div className="ver">
      <div className="head">
        <span style={{ color: colorOf(v), fontSize: 16 }}>●</span>
        <b>{v._id}</b>
        <span className={`badge ${cls}`}>{v.created_by === "seed" && v.status !== "active" ? "seed" : v.status}</span>
        {v.trigger && <span className="badge">from {v.parent_id} · trigger: {v.trigger.reason}</span>}
        {v.verdict && <span className="badge">{v.verdict.gain_over_parent_pct >= 0 ? "+" : ""}{fmt(v.verdict.gain_over_parent_pct)}% vs parent</span>}
        <span className="badge">best {fmt(v.stats?.best_fmax_mhz)} MHz · {v.stats?.valid_trials ?? 0}/{v.stats?.trials ?? 0} valid</span>
      </div>
      <p>{v.rationale}</p>
      {v.diff && <details><summary>Config diff ({v.parent_id} → {v._id})</summary><Diff text={v.diff} /></details>}
    </div>
  );
}

function Diff({ text }) {
  return <pre>{text.split("\n").map((l, i) => (
    <div key={i} className={l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}>{l}</div>))}</pre>;
}

function Chart({ trials, versions, vmap }) {
  const ref = useRef(null);
  const [w, setW] = useState(900);
  const [hover, setHover] = useState(null);
  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setW(Math.max(320, e.contentRect.width)));
    if (ref.current) ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);

  const H = 340, m = { l: 52, r: 16, t: 28, b: 46 };
  const pw = w - m.l - m.r, ph = H - m.t - m.b;
  const valid = trials.filter((t) => t.score?.valid);
  const ys = valid.map((t) => t.score.fmax_mhz);
  const lo = ys.length ? Math.floor((Math.min(...ys) - 15) / 10) * 10 : 800;
  const hi = ys.length ? Math.ceil((Math.max(...ys) + 15) / 10) * 10 : 1000;
  const n = Math.max(trials.length - 1, 1);
  const x = (i) => m.l + (i / n) * pw;
  const y = (f) => m.t + ph - ((f - lo) / (hi - lo)) * ph;
  const ticks = useMemo(() => { const s = Math.max(10, Math.ceil((hi - lo) / 5 / 10) * 10); const a = []; for (let v = lo; v <= hi; v += s) a.push(v); return a; }, [lo, hi]);

  let run = null; const bestPts = [];
  trials.forEach((t, i) => { if (t.score?.valid) run = Math.max(run ?? 0, t.score.fmax_mhz); if (run != null) bestPts.push([x(i), y(run)]); });
  const bestPath = bestPts.map(([px, py], k) => (k ? `H${px}V${py}` : `M${px},${py}`)).join("");
  const firstIdx = {}; trials.forEach((t, i) => { if (!(t.harness_version in firstIdx)) firstIdx[t.harness_version] = i; });
  const markers = versions.filter((v) => v.created_by === "evolution-loop" && v._id in firstIdx);
  const targets = trials.map((t, i) => [i, t.clock_target_mhz]).filter(([i, c], k, a) => k > 0 && c && c !== a[k - 1][1]);

  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - m.l) / pw) * n);
    setHover(i >= 0 && i < trials.length ? i : null);
  };
  const ht = hover != null ? trials[hover] : null;

  return (
    <div ref={ref} className="chartwrap">
      <div className="legend">
        {versions.map((v) => <span key={v._id}><i style={{ background: colorOf(v) }} />{v._id}</span>)}
        <span><i style={{ background: "var(--text-secondary)", borderRadius: 1, height: 2, width: 14 }} />running best</span>
        <span><i style={{ background: "var(--reject)", borderRadius: 1, width: 3 }} />rejected trial</span>
      </div>
      <svg width={w} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img"
        aria-label="fmax of each trial over time, colored by harness version">
        {ticks.map((t) => <g key={t}><line x1={m.l} x2={w - m.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" /><text x={m.l - 8} y={y(t) + 4} textAnchor="end">{t}</text></g>)}
        <text x={m.l} y={14} textAnchor="start">MHz</text>
        {markers.map((v) => <g key={v._id}>
          <line x1={x(firstIdx[v._id]) - 0.5} x2={x(firstIdx[v._id]) - 0.5} y1={m.t - 6} y2={m.t + ph} stroke="var(--text-muted)" strokeDasharray="3 3" />
          <text x={x(firstIdx[v._id]) + 4} y={m.t - 8} style={{ fill: "var(--text-secondary)" }}>{v._id} evolved</text></g>)}
        {targets.map(([i, c]) => <g key={`c${i}`}>
          <line x1={x(i) - 0.5} x2={x(i) - 0.5} y1={m.t + 10} y2={m.t + ph} stroke="var(--s8)" strokeDasharray="1 3" />
          <text x={x(i) + 4} y={m.t + 20} style={{ fill: "var(--text-secondary)" }}>target → {c} MHz</text></g>)}
        <path d={bestPath} fill="none" stroke="var(--text-secondary)" strokeWidth="2" />
        {trials.map((t, i) => t.score?.valid ? null :
          <line key={t._id} x1={x(i)} x2={x(i)} y1={H - m.b + 8} y2={H - m.b + 18} stroke="var(--reject)" strokeWidth="2" strokeLinecap="round" />)}
        <text x={m.l} y={H - 8} textAnchor="start">trials in order →   (ticks below axis = rejected by testbench / flow)</text>
        {trials.map((t, i) => t.score?.valid ?
          <circle key={t._id} cx={x(i)} cy={y(t.score.fmax_mhz)} r={hover === i ? 6 : 4.5} fill={colorOf(vmap[t.harness_version])}
            stroke="var(--surface-1)" strokeWidth="2" /> : null)}
        {ht && <line x1={x(hover)} x2={x(hover)} y1={m.t} y2={m.t + ph} stroke="var(--border)" />}
      </svg>
      {ht && (
        <div className="tip" style={{ left: Math.min(x(hover) + 12, w - 330), top: 40 }}>
          <div><b>{ht._id}</b> · {ht.harness_version} · {ht.diagnosis?.fix_family || "–"}</div>
          <div>{ht.score?.valid ? <>fmax <b>{fmt(ht.score.fmax_mhz)} MHz</b> · WNS {fmt(ht.stages?.tier2?.wns_ns, 3)} ns</> : <>rejected: {ht.score?.reject_reason}</>}</div>
          {ht.goal && <div style={{ color: "var(--text-secondary)", marginTop: 4 }}>{ht.goal.slice(0, 200)}</div>}
        </div>)}
    </div>
  );
}
