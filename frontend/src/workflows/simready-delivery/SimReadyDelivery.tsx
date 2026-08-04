"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import JobProgress from "../JobProgress";
import Tip from "../Tip";
import DeliveryReport, { PackageResult } from "./DeliveryReport";
import type { AssetRec, Classify, Report } from "./DeliveryReport";

const WF = "nvidia-simready-delivery";

// 권장값 — 옵션 라벨에 "(권장)" 표시용. 설명은 각 필드의 ⓘ(Tip) 호버로 제공.
const REC = { profile: "Prop-Robotics-Physx", version: "1.0.0", license: "CC-BY-4.0", unit: "auto" };
const LICENSE_OPTS = ["CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0"];
const rec = (v: string, r: string) => (v === r ? " (권장)" : "");

// 피처 한글 라벨·검증 결과 UI는 DeliveryReport(공용)에 있다 — 파이프라인 스텝과 같은 화면을 쓰기 위함.
// 검증 진행 중 '지금 점검 중' 단계(문서 Step 3~4 점검 묶음) — 라이브 표시용
const STAGE_STEPS = ["형상·단위", "메타데이터", "충돌(sdf)", "강체(멀티바디)", "재질(MDL)", "그래스프", "경로 앵커·텍스처"];

type Preflight = { ok: boolean; passed?: number; total?: number; available: boolean; message?: string; checks: { name: string; passed: boolean; source: string }[] };
type Engine = { name: string; rules: string; license: string; packager: string; note: string };
type Applied = { codes: string[]; label: string; explanation: string };
type Cfg = Record<string, unknown>;

export default function SimReadyDelivery({ manifest }: WorkflowModuleProps) {
  const [profiles, setProfiles] = useState<Record<string, string[]>>({});
  const [available, setAvailable] = useState<boolean | null>(null);
  const [availMsg, setAvailMsg] = useState("");
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [pfBusy, setPfBusy] = useState(false);
  const [engine, setEngine] = useState<Engine | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [profile, setProfile] = useState("Prop-Robotics-Physx");
  const [version, setVersion] = useState("1.0.0");
  const [pkgName, setPkgName] = useState("asset_a01");
  const [license, setLicense] = useState("CC-BY-4.0");
  const [assetKind, setAssetKind] = useState("prop");
  const [unit, setUnit] = useState("auto");
  const [graspText, setGraspText] = useState("");
  const [jointsExpected, setJointsExpected] = useState(false);   // 가동부(조인트) 있어야 하는 에셋인지(사용자 선언)

  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [live, setLive] = useState(0);   // '지금 점검 중' 스텝 인덱스(검증 진행 애니메이션)
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [classify, setClassify] = useState<Classify | null>(null);
  const [applied, setApplied] = useState<Applied[] | null>(null);
  const [pkg, setPkg] = useState<{ ok: boolean; log: string; no_wrapp: boolean; asset: AssetRec | null; message?: string; hash_ok?: boolean | null; report_in_pkg?: boolean; evidence?: boolean } | null>(null);
  const acRef = useRef<AbortController | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/workflows/${WF}/profiles`, { headers: authHeaders() });
        const j = await r.json();
        setProfiles(j.profiles ?? {});
        setEngine(j.engine ?? null);
        setAvailable(!!j.available); setAvailMsg(j.message ?? "");
        if (j.available) runPreflight();
      } catch { setAvailable(false); setAvailMsg("프로파일 조회 실패"); }
    })();
  }, []); // eslint-disable-line

  // 검증 진행 중 스텝을 하나씩 진행 표시(서브프로세스는 원자적이라 단계 진행을 시각화).
  useEffect(() => {
    if (!busy || !phase.includes("검증")) { setLive(0); return; }
    setLive(0);
    const id = setInterval(() => setLive((s) => Math.min(s + 1, STAGE_STEPS.length - 1)), 550);
    return () => clearInterval(id);
  }, [busy, phase]);

  async function runPreflight() {
    setPfBusy(true);
    try {
      const r = await fetch(`${API_BASE}/api/workflows/${WF}/preflight`, { headers: authHeaders() });
      setPreflight(await r.json());
    } catch { /* ignore */ } finally { setPfBusy(false); }
  }

  const versions = profiles[profile] ?? ["1.0.0"];
  useEffect(() => { if (!versions.includes(version)) setVersion(versions[0]); }, [profile]); // eslint-disable-line

  async function call<T>(path: string, fd: FormData, ph: string): Promise<T> {
    setBusy(true); setPhase(ph); setError(null);
    const ac = new AbortController(); acRef.current = ac;
    try {
      return await submitAndPoll<T>(`/api/workflows/${WF}/${path}`, fd, { signal: ac.signal });
    } finally { setBusy(false); acRef.current = null; }
  }

  async function start(e: React.FormEvent) {
    e.preventDefault();
    if (!file) { setError("USD 파일을 선택하세요."); return; }
    setReport(null); setApplied(null); setPkg(null); setSession(null); setClassify(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("profile", profile); fd.append("version", version);
      fd.append("package_name", pkgName); fd.append("license_id", license);
      fd.append("asset_kind", assetKind); fd.append("source_meters_per_unit", unit);
      fd.append("grasp_points", graspText.trim());
      const r = await call<{ session_id: string; config: Cfg; report: Report; classify: Classify }>("submit", fd, "검증 중…");
      setSession(r.session_id); setReport(r.report); setClassify(r.classify ?? null);
    } catch (err) { setError(err instanceof CancelledError ? "취소됨" : String((err as Error).message)); }
  }

  async function fix(codes: string[] | "all") {
    if (!session) return;
    setApplied(null); setPkg(null);
    try {
      const fd = new FormData();
      fd.append("session_id", session);
      fd.append("codes", codes === "all" ? "all" : JSON.stringify(codes));
      const r = await call<{ applied: Applied[]; report: Report }>("fix", fd, "수정·재검증 중…");
      setApplied(r.applied); setReport(r.report);
    } catch (err) { setError(String((err as Error).message)); }
  }

  async function downloadUsd() {
    if (!session) return;
    try {
      const fd = new FormData(); fd.append("session_id", session);
      const r = await call<{ asset: AssetRec }>("download-usd", fd, "USD 묶는 중…");
      await downloadAsset(r.asset.download_url, r.asset.filename);
    } catch (err) { setError(String((err as Error).message)); }
  }

  async function enrich() {
    if (!session) return;
    setApplied(null); setPkg(null);
    try {
      const fd = new FormData(); fd.append("session_id", session);
      const r = await call<{ applied: Applied[]; report: Report }>("enrich", fd, "물리·그래스프 보강 중…");
      setApplied(r.applied); setReport(r.report);
    } catch (err) { setError(String((err as Error).message)); }
  }

  async function doPackage() {
    if (!session) return;
    setPkg(null);
    try {
      const fd = new FormData(); fd.append("session_id", session);
      const r = await call<{ ok: boolean; log: string; no_wrapp: boolean; asset: AssetRec | null; message?: string; report: Report; hash_ok?: boolean | null; report_in_pkg?: boolean; evidence?: boolean }>("package", fd, "패키지 생성 중…");
      setPkg({ ok: r.ok, log: r.log ?? "", no_wrapp: r.no_wrapp, asset: r.asset, message: r.message, hash_ok: r.hash_ok, report_in_pkg: r.report_in_pkg, evidence: r.evidence });
      if (r.report) setReport(r.report);
    } catch (err) { setError(String((err as Error).message)); }
  }

  // deliverable = 남은 미충족이 optional(면제) 뿐 → evidence 모드로 패키지 가능.
  const canPackage = !!report?.deliverable && classify?.gate_level !== "block";

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        {available === false && (
          <p className="err">⚠ 이 머신에 SimReady Foundation 환경이 없습니다 — {availMsg}</p>
        )}
        {available && (
          <div className="row" style={{ alignItems: "center", gap: 8, fontSize: 12 }}>
            <span className="muted">환경 자가검사:</span>
            {pfBusy ? <span className="muted">확인 중…</span>
              : preflight ? (
                <span style={{ color: preflight.ok ? "var(--vsc-accent,#2e7d32)" : "#b00020" }}
                  title={preflight.checks?.filter((c) => !c.passed).map((c) => c.name).join("\n") || "모두 통과"}>
                  {preflight.ok ? "✓" : "✗"} {preflight.passed}/{preflight.total} (SPEC↔NVIDIA 소스·_bom 패치·Pillow)
                </span>
              ) : <span className="muted">—</span>}
            <button type="button" className="ghost" style={{ padding: "1px 8px", fontSize: 11 }} onClick={runPreflight} disabled={pfBusy}>다시 검사</button>
          </div>
        )}
        {available && engine && (
          <div style={{ marginTop: 6, padding: "7px 10px", border: "1px solid var(--vsc-border)", borderRadius: 6, fontSize: 11.5, background: "var(--vsc-bg-subtle,#f3f6f4)" }}>
            <div>🟩 판정 엔진 <b>{engine.name}</b> · 규칙 <span className="muted">{engine.rules}</span></div>
            <div>📦 패키징 <b>{engine.packager}</b> · <span className="muted">{engine.license}</span></div>
            <div className="muted" style={{ marginTop: 2 }}>{engine.note}</div>
          </div>
        )}
        <form onSubmit={start}>
          <label>USD 파일 (.usd/.usda/.usdc/.usdz)</label>
          <input type="file" accept=".usd,.usda,.usdc,.usdz" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <div className="row" style={{ gap: 16, marginTop: 8, flexWrap: "wrap" }}>
            <div>
              <label>납품 프로파일<Tip t="어떤 시뮬 규격으로 검증할지 정합니다. 잘 모르면 Physx(권장)." /></label>
              <select value={profile} onChange={(e) => setProfile(e.target.value)}>
                {Object.keys(profiles).map((p) => <option key={p} value={p}>{p}{rec(p, REC.profile)}</option>)}
              </select>
            </div>
            <div>
              <label>버전<Tip t="납품 규격 버전. 보통 1.0.0(권장)." /></label>
              <select value={version} onChange={(e) => setVersion(e.target.value)}>
                {versions.map((v) => <option key={v} value={v}>{v}{rec(v, REC.version)}</option>)}
              </select>
            </div>
            <div>
              <label>패키지 이름<Tip t="납품 패키지에 붙일 이름." /></label>
              <input value={pkgName} onChange={(e) => setPkgName(e.target.value)} style={{ width: 140 }} />
            </div>
            <div>
              <label>입력 단위<Tip t="원본 좌표의 크기 단위. 보통 자동(권장)이면 됩니다." /></label>
              <select value={unit} onChange={(e) => setUnit(e.target.value)}>
                <option value="auto">자동 (파일 단위 사용)</option>
                <option value="0.001">mm 강제 (0.001)</option>
                <option value="0.01">cm 강제 (0.01)</option>
                <option value="1">m 강제 (1)</option>
              </select>
            </div>
          </div>
          <div className="row" style={{ gap: 16, marginTop: 8, flexWrap: "wrap" }}>
            <div><label>라이선스<Tip t="에셋 배포 라이선스. 보통 CC-BY-4.0(권장)." /></label>
              <select value={license} onChange={(e) => setLicense(e.target.value)} style={{ width: 150 }}>
                {LICENSE_OPTS.map((l) => <option key={l} value={l}>{l}{rec(l, REC.license)}</option>)}
              </select></div>
          </div>
          <label style={{ marginTop: 8 }}>그래스프 포인트 (선택)<Tip t="로봇 그리퍼가 잡는 위치(선). 비우면 임시값으로 채웁니다." /></label>
          <input value={graspText} onChange={(e) => setGraspText(e.target.value)} placeholder='비워두면 placeholder. 예: [[-0.45,0,0.9],[0.45,0,0.9]]' />
          <label style={{ marginTop: 8, display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 400 }}>
            <input type="checkbox" checked={jointsExpected} onChange={(e) => setJointsExpected(e.target.checked)} />
            이 에셋은 가동부(조인트)가 있어야 함<Tip t="문/서랍/바퀴처럼 움직이는 부품이 있는 에셋이면 체크. 체크하면 아래 표에서 조인트가 '필수'로 검사되고, 체크 안 하면 '불필요'로 봅니다." />
          </label>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy || available === false}>{busy ? "처리 중…" : "▶ 납품 검증 시작"}</button>
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} hint={phase} etaSec={20} />
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {/* 검증 진행 중 — 스텝을 하나씩 점검하는 표시 */}
      {busy && phase.includes("검증") && (
        <div className="card">
          <label style={{ margin: 0 }}>🔍 {phase.includes("재검증") ? "재검증" : "검증"} 진행 중 — 스텝 점검</label>
          <div style={{ marginTop: 8 }}>
            {STAGE_STEPS.map((s, i) => (
              <div key={s} className="row" style={{ alignItems: "center", gap: 8, fontSize: 12.5, padding: "2px 0", opacity: i <= live ? 1 : 0.4 }}>
                <span style={{ width: 18, textAlign: "center" }}>
                  {i < live ? "✓" : i === live ? "●" : "○"}
                </span>
                <span style={{ fontWeight: i === live ? 600 : 400 }}>{i + 1}. {s}</span>
                {i === live && <span className="muted" style={{ fontSize: 11 }}>점검 중…</span>}
              </div>
            ))}
          </div>
          <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>NVIDIA simready-validate 가 프로파일 필수 피처를 순서대로 점검합니다.</p>
        </div>
      )}

      {report && !busy && (
        <div className="card">
          <DeliveryReport
            report={report}
            classify={classify}
            jointsExpected={jointsExpected}
            busy={busy}
            onFix={(codes) => fix(codes)}
            onEnrich={enrich}
            onDownloadUsd={downloadUsd}
            onPackage={doPackage}
            canPackage={canPackage}
            packageBlockedNote={classify?.gate_level === "block" ? "이 에셋 종류는 납품 프로파일이 없어 패키지 불가" : undefined}
          />
        </div>
      )}

      {/* 수정 내역 */}
      {applied && applied.length > 0 && (
        <div className="card">
          <label>해결한 항목 — 어떻게 고쳤나</label>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
            {applied.map((a, i) => (
              <li key={i} style={{ marginBottom: 4 }}>
                <span style={{ fontFamily: "monospace", color: "var(--vsc-accent,#2e7d32)" }}>{a.codes.join(", ")}</span>
                {" "}<b>{a.label}</b> — {a.explanation}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 패키지 결과 */}
      {pkg && (
        <div className="card">
          <PackageResult
            pkg={pkg}
            filename={pkg.asset?.filename}
            bytes={pkg.asset?.bytes}
            onDownload={pkg.asset ? () => downloadAsset(pkg.asset!.download_url, pkg.asset!.filename) : undefined}
          />
        </div>
      )}
    </>
  );
}
