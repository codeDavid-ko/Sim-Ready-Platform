"use client";

import { useEffect, useRef, useState } from "react";
import { apiJson, blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Row = {
  part: string;
  material?: string;
  mdl?: string;
  mass_kg?: number;
  density?: number;
  static_friction?: number;
  restitution?: number;
};
type Result = {
  engine: string;
  part_count: number;
  rows: Row[];
  step_asset: AssetRec | null;
  material_asset: AssetRec | null;
  material_preview?: AssetRec | null;
  physics_asset: AssetRec | null;
};

const WF = "trinix-simready";

export default function TrinixSimready({ manifest }: WorkflowModuleProps) {
  const [text, setText] = useState("");
  const [images, setImages] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [ready, setReady] = useState<boolean | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);
  const refs = useRef<string[]>([]);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    apiJson<{ ready: boolean }>(`/api/workflows/${WF}/ready`).then((r) => setReady(r.ready)).catch(() => setReady(null));
    return () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); };
  }, []);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setGlbSrc(null);
    if (!text.trim() && images.length === 0) { setError("텍스트 설명 또는 참조 이미지를 입력하세요."); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("text", text.trim());
      for (const img of images) fd.append("images", img);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd);
      setResult(r);
      if (r.material_preview?.download_url) {
        try { const u = await blobUrl(r.material_preview.download_url); refs.current.push(u); setGlbSrc(u); } catch { /* */ }
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
        {ready === false && (
          <p className="err">Trinix 가 준비되지 않았습니다 — .env 의 TRINIX_AI_TOKEN 과 라이브 페어링 세션이 필요합니다.</p>
        )}
        <form onSubmit={run}>
          <label>설명/요구/치수 (텍스트)</label>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4}
            placeholder="예: 알루미늄 프레임에 고무 발 4개가 달린 받침대 / 또는 도면 설명" />
          <label>참조 이미지 (선택 · 도면/사진, 복수)</label>
          <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
          <p className="muted">Trinix 3D(STEP) → 부품별 재질(vMaterials) → 질량·물성까지 한 번에 — <b>수 분</b> 소요. {images.length > 0 ? `이미지 ${images.length}장 첨부됨.` : ""}</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy || ready === false}>{busy ? "파이프라인 실행 중… (수 분)" : "모델링 → 재질·물성 실행"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>재질 미리보기 (PBR 근사 · 마우스 회전)</label>
              <model-viewer src={glbSrc} camera-controls auto-rotate shadow-intensity="1"
                style={{ width: "100%", height: "360px", background: "#0d1117", borderRadius: "8px" }} />
              <p className="muted">{result.engine}</p>
              {result.material_asset && <SpinViewer assetId={result.material_asset.id} label="🖱 인터랙티브 RTX 뷰어 (실제 재질 · 드래그 회전)" />}
            </div>
          )}
          <div className="card">
            <label>부품별 재질 · 물성 — {result.part_count}개</label>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #3c3c3c" }}>
                    <th style={{ padding: "6px 8px" }}>부품</th>
                    <th style={{ padding: "6px 8px" }}>재질(vMaterials)</th>
                    <th style={{ padding: "6px 8px" }}>질량(kg)</th>
                    <th style={{ padding: "6px 8px" }}>밀도</th>
                    <th style={{ padding: "6px 8px" }}>μs</th>
                    <th style={{ padding: "6px 8px" }}>e</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #2d2d30" }} title={r.mdl ?? ""}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{r.part}</td>
                      <td style={{ padding: "6px 8px" }}>{r.material ?? "—"}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.mass_kg)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.density)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.static_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(r.restitution)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card">
            <label>결과 다운로드</label>
            <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
              {result.step_asset && <button className="ghost" onClick={() => downloadFile(result.step_asset!.download_url, result.step_asset!.filename)}>STEP (형상)</button>}
              {result.material_asset && <button className="ghost" onClick={() => downloadFile(result.material_asset!.download_url, result.material_asset!.filename)}>재질 USD</button>}
              {result.physics_asset && <button className="ghost" onClick={() => downloadFile(result.physics_asset!.download_url, result.physics_asset!.filename)}>물성 USD (UsdPhysics)</button>}
            </div>
            <p className="muted" style={{ marginTop: 6 }}>재질 USD(부품별 vMaterials 바인딩)와 물성 USD(질량·마찰·반발)는 각각 받습니다. 정밀 RTX 룩은 재질 USD를 “재질 추론 USD” 카드/Omniverse로 렌더하세요.</p>
          </div>
        </>
      )}
    </>
  );
}
