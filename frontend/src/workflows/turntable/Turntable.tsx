"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, blobUrl, CancelledError, downloadFile, submitAndPoll } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import JobProgress from "../JobProgress";
import Tip from "../Tip";
import NumberInput from "../NumberInput";

type Joint = { path: string; name: string; type: string; axis: string; lower: number | null; upper: number | null };

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = { video: AssetRec | null };
const WF = "turntable";
const sel: React.CSSProperties = { padding: "6px 8px" };

export default function Turntable({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".usd,.usda,.usdc,.usdz";

  const [file, setFile] = useState<File | null>(null);
  // 회전(기본)
  const [turns, setTurns] = useState(1);
  const [spinDir, setSpinDir] = useState("1");
  const [seconds, setSeconds] = useState(12);
  // 줌(옵션)
  const [zoom, setZoom] = useState(false);
  const [zoomMult, setZoomMult] = useState(2);
  const [zoomInPct, setZoomInPct] = useState(46);   // 줌인 시점(영상 %)
  const [zoomOutPct, setZoomOutPct] = useState(66);  // 줌아웃 시점(영상 %)
  // 모터(옵션)
  const [motors, setMotors] = useState(false);
  const [motorMode, setMotorMode] = useState("hold");
  const [motorDeg, setMotorDeg] = useState(90);
  // 모터 감지 결과/각도범위
  const [joints, setJoints] = useState<Joint[]>([]);
  const [motorTargets, setMotorTargets] = useState<Record<string, number>>({});
  const [detecting, setDetecting] = useState(false);
  const [detected, setDetected] = useState(false);
  // 출력
  const [res, setRes] = useState("1080");
  const [fps, setFps] = useState(24);
  const [quality, setQuality] = useState<"standard" | "hq" | "pt">("standard");  // 화질: 표준/고화질/패스트레이싱
  const [clean, setClean] = useState(true);  // 입력 자동 정리(거대 평면 제거)
  const [mode, setMode] = useState<"cinematic" | "gui">("cinematic");  // 시네마틱(현재) / GUI 화면녹화
  const [showAdv, setShowAdv] = useState(false);  // 세부 옵션 접기(기본 닫힘) — 기본값으로 바로 생성

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vid, setVid] = useState<string | null>(null);
  const [asset, setAsset] = useState<AssetRec | null>(null);
  const refs = useRef<string[]>([]);
  const acRef = useRef<AbortController | null>(null);
  // 렌더 전 사전 검사 결과(문제/유의점)
  const [inspect, setInspect] = useState<{ warnings: string[]; notes: string[]; meshes?: number } | null>(null);
  const [prog, setProg] = useState<{ phase?: string; frame?: number; total?: number } | null>(null);

  useEffect(() => () => refs.current.forEach((u) => URL.revokeObjectURL(u)), []);

  async function onFile(f: File | null) {
    setFile(f); setInspect(null); setJoints([]); setDetected(false); setError(null);
    if (!f) return;
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await fetch(`${API_BASE}/api/workflows/${WF}/inspect`, { method: "POST", headers: authHeaders(), body: fd });
      const j = await r.json();
      if (r.ok) setInspect({ warnings: j.warnings ?? [], notes: j.notes ?? [], meshes: j.meshes });
    } catch { /* best-effort */ }
  }

  const etaSec = Math.round(60 + seconds * fps * 0.6); // 부팅 + 프레임당 대략

  async function detect() {
    if (!file) { setError("먼저 USD 파일을 선택하세요."); return; }
    setError(null); setDetecting(true);
    try {
      const fd = new FormData(); fd.append("file", file);
      const r = await fetch(`${API_BASE}/api/workflows/${WF}/detect-joints`, { method: "POST", headers: authHeaders(), body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j?.detail ?? "감지 실패");
      const js: Joint[] = j.joints ?? [];
      setJoints(js); setDetected(true);
      const tg: Record<string, number> = {};
      for (const jt of js) {
        // 기본 구동각 = 열리는 '끝각'(부호 유지). limit -90~0 이면 -90 으로 활짝 연다.
        const lo = jt.lower, hi = jt.upper;
        let t = 90;
        if (lo != null && hi != null) t = Math.abs(lo) >= Math.abs(hi) ? lo : hi;
        else if (hi != null) t = hi;
        else if (lo != null) t = lo;
        tg[jt.path] = t;
      }
      setMotorTargets(tg);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setDetecting(false);
    }
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setVid(null); setAsset(null); setProg(null);
    if (!file) { setError("USD 파일을 선택하세요."); return; }
    setBusy(true);
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("turns", String(turns));
      fd.append("spin_dir", spinDir);
      fd.append("seconds", String(seconds));
      fd.append("fps", String(fps));
      fd.append("res", res);
      fd.append("zoom", String(zoom));
      fd.append("zoom_mult", String(zoomMult));
      fd.append("zoom_in", String(zoomInPct / 100));
      fd.append("zoom_out", String(zoomOutPct / 100));
      fd.append("motors", String(motors));
      fd.append("motor_mode", motorMode);
      fd.append("motor_deg", String(motorDeg));
      fd.append("motor_targets", motors ? JSON.stringify(motorTargets) : "");
      fd.append("clean", String(clean));
      fd.append("mode", mode);
      fd.append("quality", quality);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/render-submit`, fd, { signal: ac.signal, onProgress: setProg });
      if (r.video?.download_url) {
        const u = await blobUrl(r.video.download_url);
        refs.current.push(u);
        setVid(u); setAsset(r.video);
      } else {
        setError("영상이 생성되지 않았습니다.");
      }
    } catch (err) {
      setError(err instanceof CancelledError ? "취소되었습니다." : String((err as Error).message));
    } finally {
      setBusy(false);
      acRef.current = null;
    }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
          {inspect && (inspect.warnings.length > 0 || inspect.notes.length > 0) && (
            <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: 4, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>
              {inspect.warnings.map((w, i) => (
                <p key={`w${i}`} className="err" style={{ margin: "2px 0", fontSize: 12.5 }}>⚠ {w}</p>
              ))}
              {inspect.notes.map((n, i) => (
                <p key={`n${i}`} className="muted" style={{ margin: "2px 0", fontSize: 12.5 }}>ⓘ {n}</p>
              ))}
            </div>
          )}

          <div className="card-sub" style={{ marginTop: 10 }}>
            <label style={{ marginTop: 0 }}>녹화 방식<Tip t="시네마틱: Isaac을 헤드리스로 띄워 뷰포트만 깨끗하게 렌더(UI 없음, 결과물용). GUI 화면녹화: Isaac Sim 앱을 실제로 띄워 툴바·패널까지 보이는 화면을 통째로 녹화(‘진짜 Isaac에서 돌렸다’ 데모용, 회전만). GUI는 디스플레이 있는 데스크톱 세션에서만 동작하고 녹화 중 모니터를 점유합니다." /></label>
            <div className="row" style={{ gap: 16, flexWrap: "wrap" }}>
              <label style={{ fontWeight: 400, margin: 0, display: "flex", gap: 6, alignItems: "center" }}>
                <input type="radio" name="ttmode" checked={mode === "cinematic"} onChange={() => setMode("cinematic")} style={{ width: "auto" }} />
                🎬 시네마틱 (헤드리스 · 뷰포트만 · 현재)
              </label>
              <label style={{ fontWeight: 400, margin: 0, display: "flex", gap: 6, alignItems: "center" }}>
                <input type="radio" name="ttmode" checked={mode === "gui"} onChange={() => setMode("gui")} style={{ width: "auto" }} />
                🖥 GUI 화면녹화 (Isaac 앱 화면 통째 · 회전만)
              </label>
            </div>
            {mode === "gui" && (
              <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
                ⚠ 디스플레이가 있는 데스크톱 세션에서만 동작하고, <b>녹화 중 모니터를 Isaac이 점유</b>합니다(다른 창 띄우면 같이 찍힘). 회전·줌 적용 — 모터(조인트 자동구동)는 무시됩니다.
              </p>
            )}
          </div>

          <div style={{ marginTop: 10 }}>
            <button type="button" className="ghost" onClick={() => setShowAdv((v) => !v)}>
              {showAdv ? "▾ 세부 옵션 닫기" : "▸ 세부 옵션 (회전·줌·모터·출력) — 기본값으로 그냥 생성해도 됩니다"}
            </button>
          </div>
          {showAdv && (
          <div style={{ marginTop: 10 }}>
            {/* 2단: 회전 + 출력 (둘 다 내용 있어 빈칸 없음) */}
            <div className="opt-grid">
              <div className="opt card-sub">
                <label style={{ marginTop: 0 }}>🔄 턴테이블 회전 (기본)</label>
                <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
                  <label style={{ fontWeight: 400, margin: 0 }}>바퀴 수<Tip t="객체를 몇 바퀴 회전시킬지. ‘길이(초)’ 동안 이 횟수만큼 돕니다(=회전 속도). 프레임 수·렌더 시간엔 영향 없음." />&nbsp;
                    <NumberInput min={0.25} max={5} step={0.25} value={turns}
                      onChange={(n) => setTurns(n)} style={{ width: 70 }} /></label>
                  <label style={{ fontWeight: 400, margin: 0 }}>방향<Tip t="회전 방향. CCW=반시계, CW=시계." />&nbsp;
                    <select value={spinDir} onChange={(e) => setSpinDir(e.target.value)} style={sel}>
                      <option value="1">CCW(반시계)</option>
                      <option value="-1">CW(시계)</option>
                    </select></label>
                  <label style={{ fontWeight: 400, margin: 0 }}>길이(초)<Tip t="출력 영상 길이(초). 총 프레임 = 길이×FPS. 길수록 프레임↑ = 렌더 시간↑(예 12초×24fps=288프레임)." />&nbsp;
                    <NumberInput min={2} max={60} value={seconds}
                      onChange={(n) => setSeconds(n)} style={{ width: 70 }} /></label>
                </div>
              </div>

              <div className="opt card-sub">
                <label style={{ marginTop: 0 }}>🎞 출력</label>
                <div className="row" style={{ gap: 12 }}>
                  <label style={{ fontWeight: 400, margin: 0 }}>해상도<Tip t="영상 화질(가로×세로). 높을수록 프레임당 렌더가 느려집니다(720<1080<1440)." />&nbsp;
                    <select value={res} onChange={(e) => setRes(e.target.value)} style={sel}>
                      <option value="720">720p</option>
                      <option value="1080">1080p</option>
                      <option value="1440">1440p</option>
                    </select></label>
                  <label style={{ fontWeight: 400, margin: 0 }}>FPS<Tip t="초당 프레임. 높을수록 부드럽지만 총 프레임↑ = 렌더 시간↑." />&nbsp;
                    <NumberInput min={12} max={60} value={fps}
                      onChange={(n) => setFps(n)} style={{ width: 70 }} /></label>
                </div>
                {mode === "cinematic" && (
                  <label style={{ fontWeight: 400, margin: 0, marginTop: 10, display: "block" }}>화질
                    <Tip t="표준=RTX 실시간(가장 빠름) · 고화질=RTX+DLSS·반사·AO(GUI 수준, 약간 느림) · 패스트레이싱=물리기반 최고화질(매우 느림, 프레임당 샘플 누적). GUI 녹화 모드에는 적용되지 않습니다." />&nbsp;
                    <select value={quality} onChange={(e) => setQuality(e.target.value as "standard" | "hq" | "pt")} style={sel}>
                      <option value="standard">표준 (RTX 실시간)</option>
                      <option value="hq">고화질 (RTX+DLSS)</option>
                      <option value="pt">패스트레이싱 (최고화질·느림)</option>
                    </select></label>
                )}
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 10 }}>
                  <input type="checkbox" checked={clean} onChange={(e) => setClean(e.target.checked)} style={{ width: "auto" }} />
                  입력 자동 정리(거대 평면 제거)<Tip t="바닥·배경처럼 보이는 거대한 평면을 렌더 전에 제거해 피사체가 잘 잡히게 합니다(재질은 유지). 의도된 바닥이 사라지면 끄세요." />
                </label>
              </div>
            </div>

            {/* 효과: 체크 전엔 토글 한 줄(빈 박스 X), 켜면 그 아래 전체폭으로 펼침 */}
            <div className="card-sub" style={{ marginTop: 10 }}>
              <div className="row" style={{ gap: 22, flexWrap: "wrap", alignItems: "center" }}>
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 600, margin: 0 }}>
                  <input type="checkbox" checked={zoom} onChange={(e) => setZoom(e.target.checked)} style={{ width: "auto" }} />
                  🔍 줌 인/아웃 (렌즈)<Tip t="켜면 회전 도중 렌즈를 당겼다 푸는 줌 연출을 추가합니다. 끄면 일정한 화각으로만 회전." />
                </label>
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 600, margin: 0 }}>
                  <input type="checkbox" checked={motors} onChange={(e) => setMotors(e.target.checked)} style={{ width: "auto" }} />
                  ⚙️ 모터/조인트 구동 (자동 감지)<Tip t="켜면 USD 안의 drive(모터) 달린 조인트를 감지해 회전 영상 중 자동으로 여닫습니다(문·관절 등). 끄면 형상만 회전." />
                </label>
              </div>

              {zoom && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--vsc-border)" }}>
                  <div className="row" style={{ gap: 14, flexWrap: "wrap", alignItems: "center" }}>
                    <label style={{ fontWeight: 400, margin: 0 }}>줌 배율<Tip t="줌인 최대 배율. 지정한 줌인 시점에 피사체가 이 배율로 보이게 렌즈를 당겼다 줌아웃 시점에 다시 풉니다. 카메라는 고정." />&nbsp;
                      <NumberInput min={1.1} max={5} step={0.1} value={zoomMult}
                        onChange={(n) => setZoomMult(n)} style={{ width: 64 }} /> ×</label>
                    <label style={{ fontWeight: 400, margin: 0 }}>줌인 시점<Tip t="영상 길이의 몇 % 지점에서 줌인을 할지. 예: 30 이면 영상 30% 지점에서 렌즈를 당깁니다." />&nbsp;
                      <NumberInput min={0} max={100} value={zoomInPct}
                        onChange={(n) => setZoomInPct(n)} style={{ width: 64 }} /> %</label>
                    <label style={{ fontWeight: 400, margin: 0 }}>줌아웃 시점<Tip t="영상 길이의 몇 % 지점에서 다시 줌아웃(원래대로)을 할지. 줌인 시점보다 뒤여야 합니다." />&nbsp;
                      <NumberInput min={0} max={100} value={zoomOutPct}
                        onChange={(n) => setZoomOutPct(n)} style={{ width: 64 }} /> %</label>
                  </div>
                  {zoomOutPct <= zoomInPct && (
                    <p className="err" style={{ fontSize: 12, margin: "6px 0 0" }}>⚠ 줌아웃 시점은 줌인 시점보다 뒤여야 합니다(자동 보정되지만 의도와 다를 수 있어요).</p>
                  )}
                  <p className="muted" style={{ fontSize: 12, margin: "8px 0 2px" }}>
                    영상 <b>{zoomInPct}%</b> 지점에서 피사체가 ×{zoomMult}로 보이게 <b>렌즈</b>를 줌인 → 유지 → <b>{zoomOutPct}%</b> 지점에서 줌아웃. (카메라 고정, 렌즈만)
                  </p>
                  {(() => {
                    const X = (t: number) => 2 + 236 * Math.min(Math.max(t, 0), 1);
                    const r = 0.04, zi = zoomInPct / 100;
                    const zo = Math.max(zoomOutPct / 100, zi + 2 * r);
                    const pts = `${X(0)},30 ${X(zi - r)},30 ${X(zi + r)},8 ${X(zo - r)},8 ${X(zo + r)},30 ${X(1)},30`;
                    return (
                      <svg width="240" height="36" style={{ display: "block" }} aria-label="줌 곡선">
                        <polyline points={pts} fill="none" stroke="#4ec9b0" strokeWidth="2" />
                        <text x="2" y="12" fontSize="9" fill="#888">렌즈 배율</text>
                      </svg>
                    );
                  })()}
                  <p className="muted" style={{ fontSize: 11, margin: 0 }}>시작 ── 줌인({zoomInPct}%) ── 유지 ── 줌아웃({zoomOutPct}%) ── 끝</p>
                </div>
              )}

              {motors && (
                <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--vsc-border)" }}>
                  <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "center" }}>
                    <button type="button" className="ghost" onClick={detect} disabled={detecting || !file}>
                      {detecting ? "감지 중…" : "① 모터 조인트 감지"}</button>
                    <label style={{ fontWeight: 400, margin: 0 }}>모드<Tip t="모터 구동 방식. ‘열고 유지’=0→목표각으로 빨리 열고 유지. ‘왕복’=0→목표→0 반복." />&nbsp;
                      <select value={motorMode} onChange={(e) => setMotorMode(e.target.value)} style={sel}>
                        <option value="hold">열고 유지</option>
                        <option value="cycle">왕복</option>
                      </select></label>
                  </div>
                  {detected && joints.length === 0 && (
                    <p className="muted" style={{ fontSize: 12 }}>drive(모터) 달린 조인트를 못 찾았습니다 — 회전만 됩니다.</p>
                  )}
                  {joints.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <p className="muted" style={{ fontSize: 12, margin: "0 0 4px" }}>② 감지된 조인트 {joints.length}개 — <b>구동 각도(°)</b><Tip t="이 조인트를 영상에서 몇 도까지 움직일지(문 여는 각도). 기본값은 그 조인트의 limit 중 '활짝 열리는 끝각'(부호 포함). 예: limit −90~0° → −90°(완전 개방). 0에 가까우면 거의 안 움직입니다." /> — 각 조인트가 이 각도까지 열립니다:</p>
                      {joints.map((jt) => (
                        <div className="row" key={jt.path} style={{ gap: 8, fontSize: 12, alignItems: "center", padding: "2px 0" }}>
                          <span style={{ minWidth: 200 }}><b>{jt.name}</b> <span className="muted">({jt.type}/{jt.axis}{jt.lower != null ? `, 가동범위 ${jt.lower}~${jt.upper}°` : ""})</span></span>
                          <span className="muted">구동각</span>
                          <NumberInput min={-180} max={180} value={motorTargets[jt.path] ?? 90}
                            onChange={(n) => setMotorTargets((m) => ({ ...m, [jt.path]: n }))}
                            style={{ width: 80 }} /> °
                        </div>
                      ))}
                    </div>
                  )}
                  {!detected && (
                    <p className="muted" style={{ fontSize: 12 }}>※ 감지를 누르면 조인트 목록·각도를 지정할 수 있습니다.</p>
                  )}
                  <label style={{ fontWeight: 400, margin: "4px 0 0", display: "block" }}>기본각(°)<Tip t="조인트에 limit이 없거나 감지 전일 때 쓸 구동 각도(도)." />&nbsp;
                    <NumberInput min={5} max={180} value={motorDeg}
                      onChange={(n) => setMotorDeg(n)} style={{ width: 70 }} /></label>
                </div>
              )}
            </div>
          </div>
          )}

          <p className="muted" style={{ marginTop: 12 }}>
            Isaac Sim RTX 렌더 → mp4. 기본값(1바퀴·12초·1080p)으로 바로 생성되고, 회전·줌·모터·해상도는 위 <b>세부 옵션</b>에서 조정합니다. 길이·해상도·바퀴수↑면 수 분 이상 걸립니다.
          </p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "렌더 중…" : "영상 생성"}</button>
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={etaSec} progress={prog} hint="Isaac RTX" />
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {vid && asset && (
        <div className="card">
          <label>턴테이블 영상</label>
          <video src={vid} controls autoPlay loop muted playsInline
            style={{ width: "100%", borderRadius: 8, background: "#0d1117" }} />
          <p className="muted">{asset.filename} · {(asset.bytes / 1024 / 1024).toFixed(1)} MB</p>
          <button className="ghost" onClick={() => downloadFile(asset.download_url, asset.filename)}>mp4 다운로드</button>
        </div>
      )}
    </>
  );
}
