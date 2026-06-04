"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Part = {
  name: string;
  material?: string;
  confidence?: number;
  density?: number;
  volume_m3: number;
  mass_kg?: number;
  dims_mm: number[];
  volume_method: string;
  static_friction?: number;
  dynamic_friction?: number;
  restitution?: number;
  material_reasoning?: string;
  physics_reasoning?: string;
};
type Result = {
  engine: string;
  in_units: string;
  layout: string;
  llm_used: boolean;
  part_count: number;
  total_mass_kg: number;
  parts: Part[];
  clamped: { part_index: number; name: string; param: string }[];
  validation: { ran: boolean; violations: string[]; error?: string };
  asset: AssetRec | null;
  preview?: AssetRec | null;
};

const WF = "mass-physics";

export default function MassPhysics({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".stl,.step,.stp,.obj,.ply,.glb";
  const [file, setFile] = useState<File | null>(null);
  const [units, setUnits] = useState("mm");
  const [context, setContext] = useState("");
  const [layout, setLayout] = useState("assembled");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);
  const glbRef = useRef<string | null>(null);
  const [isaacVid, setIsaacVid] = useState<string | null>(null);
  const [isaacBusy, setIsaacBusy] = useState(false);
  const [isaacErr, setIsaacErr] = useState<string | null>(null);
  const isaacRefs = useRef<string[]>([]);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    return () => {
      if (glbRef.current) URL.revokeObjectURL(glbRef.current);
      isaacRefs.current.forEach((u) => URL.revokeObjectURL(u));
    };
  }, []);

  async function runIsaac() {
    if (!result?.asset) return;
    setIsaacErr(null);
    setIsaacBusy(true);
    try {
      const fd = new FormData();
      fd.append("asset_id", result.asset.id);
      const r = await submitAndPoll<{ video: { download_url: string } | null }>(
        `/api/workflows/${WF}/render-submit`, fd,
      );
      if (r.video?.download_url) {
        const u = await blobUrl(r.video.download_url);
        isaacRefs.current.push(u);
        setIsaacVid(u);
      }
    } catch (err) {
      setIsaacErr(String((err as Error).message));
    } finally {
      setIsaacBusy(false);
    }
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (!file) {
      setError("STEP/STL 파일을 선택하세요.");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("in_units", units);
      fd.append("context", context);
      fd.append("layout", layout);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd);
      setResult(r);
      if (r.preview?.download_url) {
        try {
          const url = await blobUrl(r.preview.download_url);
          if (glbRef.current) URL.revokeObjectURL(glbRef.current);
          glbRef.current = url;
          setGlbSrc(url);
        } catch { setGlbSrc(null); }
      }
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const fmt = (v?: number) => (v === undefined || v === null ? "—" : v);
  const vOk = result?.validation?.ran && result.validation.violations.length === 0;

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>STEP/STL 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <div className="row" style={{ gap: 16, marginTop: 8 }}>
            <div>
              <label>입력 단위</label>
              <select value={units} onChange={(e) => setUnits(e.target.value)}>
                <option value="mm">mm</option>
                <option value="m">m</option>
                <option value="cm">cm</option>
                <option value="in">inch</option>
              </select>
            </div>
            <div>
              <label>레이아웃</label>
              <select value={layout} onChange={(e) => setLayout(e.target.value)}>
                <option value="assembled">assembled (원좌표)</option>
                <option value="droptest">droptest (낙하·정착)</option>
              </select>
            </div>
          </div>
          <label style={{ marginTop: 8 }}>제품/맥락 힌트 (선택 — Stage1 재질분류 prior)</label>
          <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="예: 접이식 운반 박스(폴리프로필렌), 또는 산업용 강철 브래킷" />
          <p className="muted">질량·부피·관성은 형상에서 정확 계산(LLM 아님). 재질·접촉계수만 구독 Claude가 추론합니다.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "추론 중… (Stage1/2)" : "물성 추론 실행"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>형상 미리보기</label>
              <model-viewer
                src={glbSrc}
                camera-controls
                auto-rotate
                shadow-intensity="1"
                style={{ width: "100%", height: "320px", background: "#0d1117", borderRadius: "8px" }}
              />
            </div>
          )}
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>물성 결과 — {result.part_count}개 부품 · 총 질량 {result.total_mass_kg} kg</label>
              <span className={`badge ${vOk ? "ok" : ""}`}>{vOk ? "usdchecker 통과" : (result.validation.ran ? `위반 ${result.validation.violations.length}` : "검증 미실행")}</span>
            </div>
            <p className="muted">{result.engine}{result.llm_used ? "" : " · ⚠ 인증 없음: 재질 steel 기본값"}</p>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #e3e6ea" }}>
                    <th style={{ padding: "6px 8px" }}>부품</th>
                    <th style={{ padding: "6px 8px" }}>재질</th>
                    <th style={{ padding: "6px 8px" }}>밀도</th>
                    <th style={{ padding: "6px 8px" }}>부피 (m³)</th>
                    <th style={{ padding: "6px 8px" }}>질량 (kg)</th>
                    <th style={{ padding: "6px 8px" }}>μs</th>
                    <th style={{ padding: "6px 8px" }}>μd</th>
                    <th style={{ padding: "6px 8px" }}>e</th>
                    <th style={{ padding: "6px 8px" }}>부피법</th>
                  </tr>
                </thead>
                <tbody>
                  {result.parts.map((p, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #eef0f2" }} title={[p.material_reasoning, p.physics_reasoning].filter(Boolean).join(" / ")}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{p.name}</td>
                      <td style={{ padding: "6px 8px" }}>{p.material ?? "—"}{p.confidence !== undefined ? <span className="muted" style={{ fontSize: 10 }}> ({Math.round((p.confidence ?? 0) * 100)}%)</span> : null}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.density)}</td>
                      <td style={{ padding: "6px 8px" }}>{p.volume_m3?.toExponential(2)}</td>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{fmt(p.mass_kg)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.static_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.dynamic_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.restitution)}</td>
                      <td style={{ padding: "6px 8px" }}>
                        <span className="muted" style={{ fontSize: 11 }}>{p.volume_method}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {result.parts.some((p) => p.volume_method !== "mesh_exact") && (
              <p className="muted" style={{ marginTop: 6 }}>⚠ 일부 부품은 비폐쇄 메시 → convex_hull/bbox 부피(중공 과대 가능). STEP 입력이면 정확합니다.</p>
            )}
            {result.clamped.length > 0 && (
              <p className="muted" style={{ marginTop: 6 }}>접촉계수 clamp: {result.clamped.map((c) => `${c.name}.${c.param}`).join(", ")}</p>
            )}
            <p className="muted" style={{ marginTop: 6 }}>μs=정지마찰, μd=운동마찰, e=반발. 행에 커서를 올리면 추론 근거가 보입니다. <b>mass=density×volume</b>(형상 정확값).</p>
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 USD (UsdPhysics · {result.layout})</label>
              {result.asset && <span className="badge ok">저장소 등록됨</span>}
            </div>
            {result.asset && (
              <>
                <p className="muted">{result.asset.filename} · {(result.asset.bytes / 1024).toFixed(1)} KB · metersPerUnit=1, Z-up, 중력 9.81</p>
                <div className="row" style={{ marginTop: 8, gap: 8 }}>
                  <button className="ghost" onClick={() => downloadFile(result.asset!.download_url, result.asset!.filename)}>USD 다운로드</button>
                  <button className="ghost" onClick={runIsaac} disabled={isaacBusy}>{isaacBusy ? "렌더 중… (Isaac Sim)" : "Omniverse로 렌더"}</button>
                </div>
              </>
            )}
            {isaacErr && <p className="err">{isaacErr}</p>}
            {isaacVid && (
              <video src={isaacVid} controls autoPlay loop muted playsInline style={{ width: "100%", borderRadius: 8, background: "#0d1117", marginTop: 8 }} />
            )}
          </div>
          {result.validation.violations.length > 0 && (
            <div className="card">
              <label>usdchecker 위반</label>
              <pre>{result.validation.violations.join("\n")}</pre>
            </div>
          )}
        </>
      )}
    </>
  );
}
