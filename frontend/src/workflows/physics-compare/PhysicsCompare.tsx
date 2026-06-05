"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Side = { material?: string; density?: number; mass_kg?: number; static_friction?: number; restitution?: number };
type Row = { part: string; ndot: Side; nvidia: Side };
type Result = {
  input: string;
  in_units: string;
  ndot_total_mass_kg: number;
  rows: Row[];
  ndot_asset: AssetRec | null;
  ndot_preview?: AssetRec | null;
  nvidia_asset: AssetRec | null;
  nvidia_status: string;
  render_ndot?: AssetRec | null;
  render_nvidia?: (AssetRec & { id: string }) | null;
};

const WF = "physics-compare";

export default function PhysicsCompare({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ?? ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [context, setContext] = useState("");
  const [images, setImages] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [glb, setGlb] = useState<string | null>(null);
  const refs = useRef<string[]>([]);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    return () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); };
  }, []);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setGlb(null);
    if (!file) { setError("USD 파일을 선택하세요."); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("context", context);
      for (const img of images) fd.append("images", img);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/compare-submit`, fd);
      setResult(r);
      if (r.ndot_preview?.download_url) {
        try { const u = await blobUrl(r.ndot_preview.download_url); refs.current.push(u); setGlb(u); } catch { /* */ }
      }
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const fmt = (v?: number) => (v === undefined || v === null ? "—" : v);

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <label>맥락 힌트 (선택 — NdotLight Stage1 재질분류에 사용)</label>
          <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="예: 산업용 강철 브래킷 / 접이식 플라스틱 박스" />
          <label>참조 이미지 (선택 · NdotLight 쪽에만 전달)</label>
          <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
          <p className="muted">두 물리 엔진을 모두 실행합니다 — <b>수 분</b> 소요(특히 NVIDIA 쪽 WSL 렌더). STEP/STL은 ‘형상 → USD 변환’ 카드로 먼저 USD로 바꾸세요.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "두 엔진 실행 중… (수 분)" : "물리 비교 실행"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glb && (
            <div className="card">
              <label>형상 미리보기</label>
              <model-viewer src={glb} camera-controls auto-rotate shadow-intensity="1"
                style={{ width: "100%", height: "300px", background: "#0d1117", borderRadius: "8px" }} />
            </div>
          )}
          <div className="card">
            <label>부품별 물리 비교 — {result.input} ({result.in_units})</label>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #3c3c3c" }}>
                    <th style={{ padding: "6px 8px" }} rowSpan={2}>부품</th>
                    <th style={{ padding: "6px 8px", borderLeft: "1px solid #3c3c3c" }} colSpan={4}>NdotLight (고정표·형상정확)</th>
                    <th style={{ padding: "6px 8px", borderLeft: "1px solid #3c3c3c" }} colSpan={3}>NVIDIA (VLM 추정)</th>
                  </tr>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #3c3c3c", color: "#9d9d9d" }}>
                    <th style={{ padding: "4px 8px", borderLeft: "1px solid #3c3c3c" }}>재질</th>
                    <th style={{ padding: "4px 8px" }}>밀도</th>
                    <th style={{ padding: "4px 8px" }}>질량kg</th>
                    <th style={{ padding: "4px 8px" }}>μs/e</th>
                    <th style={{ padding: "4px 8px", borderLeft: "1px solid #3c3c3c" }}>재질</th>
                    <th style={{ padding: "4px 8px" }}>밀도</th>
                    <th style={{ padding: "4px 8px" }}>질량kg</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #2d2d30" }}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{r.part}</td>
                      <td style={{ padding: "6px 8px", borderLeft: "1px solid #3c3c3c" }}>{r.ndot.material ?? "—"}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.ndot.density)}</td>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{fmt(r.ndot.mass_kg)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.ndot.static_friction)}/{fmt(r.ndot.restitution)}</td>
                      <td style={{ padding: "6px 8px", borderLeft: "1px solid #3c3c3c" }}>{r.nvidia.material ?? "—"}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.nvidia.density)}</td>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{fmt(r.nvidia.mass_kg)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted" style={{ marginTop: 8 }}>
              NdotLight 총 질량 {result.ndot_total_mass_kg} kg (형상 정확 부피 × 고정표 밀도). NVIDIA는 VLM이 부품별 밀도·질량을 직접 추정(질량은 루트에 집계되어 부품칸이 비어 보일 수 있음). μs=정지마찰, e=반발.
            </p>
          </div>

          {(result.render_ndot || result.render_nvidia) && (
            <div className="card">
              <label>Omniverse RTX 비교 (드래그 회전)</label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>NdotLight (UsdPhysics)</div>
                  {result.render_ndot ? <SpinViewer assetId={result.render_ndot.id} label="🖱 RTX 뷰어" /> : <p className="muted">없음</p>}
                </div>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>NVIDIA content-physics</div>
                  {result.render_nvidia ? <SpinViewer assetId={result.render_nvidia.id} label="🖱 RTX 뷰어" /> : <p className="muted">없음</p>}
                </div>
              </div>
            </div>
          )}

          <div className="card">
            <label>결과 USD 다운로드</label>
            <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
              {result.ndot_asset && <button className="ghost" onClick={() => downloadFile(result.ndot_asset!.download_url, result.ndot_asset!.filename)}>NdotLight 물성 USD</button>}
              {result.nvidia_asset && <button className="ghost" onClick={() => downloadFile(result.nvidia_asset!.download_url, result.nvidia_asset!.filename)}>NVIDIA 물성 USD</button>}
              {result.nvidia_status && <span className="badge ok">{result.nvidia_status}</span>}
            </div>
          </div>
        </>
      )}
    </>
  );
}
