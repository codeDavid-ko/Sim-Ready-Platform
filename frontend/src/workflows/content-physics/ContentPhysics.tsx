"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Phys = {
  mass?: number;
  density?: number;
  static_friction?: number;
  dynamic_friction?: number;
  restitution?: number;
};
type Result = {
  engine: string;
  status: string;
  materials: Record<string, string>;
  physics: Record<string, Phys>;
  asset: AssetRec | null;
  preview?: AssetRec | null;
  usdz_asset?: (AssetRec & { id: string }) | null;
  log_tail: string;
};

const WF = "content-physics";

export default function ContentPhysics({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
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
    if (!result?.usdz_asset) return;
    setIsaacErr(null);
    setIsaacBusy(true);
    try {
      const fd = new FormData();
      fd.append("asset_id", result.usdz_asset.id);
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
      setError("USD 파일을 선택하세요.");
      return;
    }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
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
  const physRows = result ? Object.entries(result.physics) : [];

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="muted">WSL2에서 멀티뷰 렌더 + 부품 식별 후 질량·마찰·반발을 추론해 <b>UsdPhysics</b>를 적용합니다. <b>수 분</b> 소요.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "실행 중… (수 분)" : "물리 추론 실행 (NVIDIA)"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>형상 미리보기 (PBR 근사)</label>
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
              <label style={{ margin: 0 }}>부품별 물리 속성</label>
              <span className="badge ok">{result.status}</span>
            </div>
            <p className="muted">{result.engine}</p>
            {physRows.length === 0 ? (
              <p className="muted">UsdPhysics 속성이 출력 USD에서 발견되지 않았습니다. (로그 확인)</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #e3e6ea" }}>
                    <th style={{ padding: "6px 8px" }}>부품</th>
                    <th style={{ padding: "6px 8px" }}>질량 (kg)</th>
                    <th style={{ padding: "6px 8px" }}>밀도 (kg/m³)</th>
                    <th style={{ padding: "6px 8px" }}>정지 마찰</th>
                    <th style={{ padding: "6px 8px" }}>운동 마찰</th>
                    <th style={{ padding: "6px 8px" }}>반발</th>
                  </tr>
                </thead>
                <tbody>
                  {physRows.map(([part, p]) => (
                    <tr key={part} style={{ borderBottom: "1px solid #eef0f2" }}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{part}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.mass)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.density)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.static_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.dynamic_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.restitution)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {Object.keys(result.materials).length > 0 && (
              <p className="muted" style={{ marginTop: 8 }}>
                재질 추정: {Object.entries(result.materials).map(([k, v]) => `${k}→${v}`).join(", ")}
              </p>
            )}
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 USD (UsdPhysics 적용)</label>
              {result.asset && <span className="badge ok">저장소 등록됨</span>}
            </div>
            {result.asset && (
              <>
                <p className="muted">{result.asset.filename} · {(result.asset.bytes / 1024).toFixed(1)} KB</p>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="ghost" onClick={() => downloadFile(result.asset!.download_url, result.asset!.filename)}>USD 다운로드</button>
                </div>
              </>
            )}
          </div>
          {result.usdz_asset && (
            <div className="card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <label style={{ margin: 0 }}>Omniverse 렌더 (360° 회전 · RTX)</label>
                <button className="ghost" onClick={runIsaac} disabled={isaacBusy}>
                  {isaacBusy ? "렌더 중… (Isaac Sim)" : "Omniverse로 렌더"}
                </button>
              </div>
              <p className="muted">결과를 USDZ로 묶어 Isaac Sim RTX로 360° 회전 렌더. (약 1~2분)</p>
              {isaacErr && <p className="err">{isaacErr}</p>}
              {isaacVid && (
                <video src={isaacVid} controls autoPlay loop muted playsInline style={{ width: "100%", borderRadius: 8, background: "#0d1117" }} />
              )}
            </div>
          )}
          {result.log_tail && (
            <div className="card">
              <label>실행 로그 (끝부분)</label>
              <pre>{result.log_tail}</pre>
            </div>
          )}
        </>
      )}
    </>
  );
}
