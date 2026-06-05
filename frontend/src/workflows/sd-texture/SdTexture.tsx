"use client";

import { useEffect, useRef, useState } from "react";
import { apiJson, blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = {
  engine: string;
  prompt: string;
  maps: { albedo?: AssetRec; normal?: AssetRec; roughness?: AssetRec };
  preview?: AssetRec | null;
  usd_asset?: AssetRec | null;
  usdz_asset?: AssetRec | null;
};

const WF = "sd-texture";

export default function SdTexture({ manifest }: WorkflowModuleProps) {
  const [prompt, setPrompt] = useState("");
  const [size, setSize] = useState(768);
  const [steps, setSteps] = useState(4);
  const [seed, setSeed] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [ready, setReady] = useState<boolean | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);
  const [thumbs, setThumbs] = useState<{ albedo?: string; normal?: string; roughness?: string }>({});
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
    setThumbs({});
    if (!prompt.trim()) { setError("프롬프트를 입력하세요."); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("prompt", prompt.trim());
      fd.append("size", String(size));
      fd.append("steps", String(steps));
      fd.append("seed", String(seed));
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd);
      setResult(r);
      if (r.preview?.download_url) {
        try { const u = await blobUrl(r.preview.download_url); refs.current.push(u); setGlbSrc(u); } catch { /* */ }
      }
      const t: { albedo?: string; normal?: string; roughness?: string } = {};
      for (const k of ["albedo", "normal", "roughness"] as const) {
        const rec = r.maps[k];
        if (rec?.download_url) { try { const u = await blobUrl(rec.download_url); refs.current.push(u); t[k] = u; } catch { /* */ } }
      }
      setThumbs(t);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        {ready === false && (
          <p className="err">로컬 SD 환경(~/sd_texture_venv)이 아직 준비되지 않았습니다. 설치 완료 후 사용 가능합니다.</p>
        )}
        <form onSubmit={run}>
          <label>프롬프트 (영어 권장 — SD는 영어로 학습됨)</label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
            placeholder="e.g. rusted brushed steel plate, weathered, scratches" />
          <div className="row" style={{ gap: 16, marginTop: 8, flexWrap: "wrap" }}>
            <div><label>해상도</label>
              <select value={size} onChange={(e) => setSize(Number(e.target.value))}>
                <option value={512}>512</option><option value={768}>768</option><option value={1024}>1024</option>
              </select></div>
            <div><label>스텝 (SD-Turbo: 1~4)</label>
              <input type="number" min={1} max={8} value={steps} onChange={(e) => setSteps(Number(e.target.value))} style={{ width: 90 }} /></div>
            <div><label>시드</label>
              <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} style={{ width: 120 }} /></div>
          </div>
          <p className="muted">로컬 GPU에서 무료로 생성됩니다(외부 키 불필요). 첫 실행은 모델 다운로드로 몇 분 더 걸릴 수 있습니다.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy || ready === false}>{busy ? "생성 중…" : "텍스처 생성"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>머티리얼 미리보기 (타일 · 실제 생성 텍스처 · 마우스 회전)</label>
              <model-viewer src={glbSrc} camera-controls auto-rotate shadow-intensity="1"
                style={{ width: "100%", height: "340px", background: "#0d1117", borderRadius: "8px" }} />
              <p className="muted">{result.engine}</p>
            </div>
          )}
          <div className="card">
            <label>PBR 텍스처 맵</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
              {(["albedo", "normal", "roughness"] as const).map((k) => (
                <div key={k}>
                  <div className="muted" style={{ marginBottom: 4 }}>{k}</div>
                  {thumbs[k] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={thumbs[k]} alt={k} style={{ width: "100%", borderRadius: 6, background: "#0d1117", imageRendering: "auto" }} />
                  ) : <p className="muted">—</p>}
                  {result.maps[k] && (
                    <button className="ghost" style={{ marginTop: 6 }} onClick={() => downloadFile(result.maps[k]!.download_url, result.maps[k]!.filename)}>PNG</button>
                  )}
                </div>
              ))}
            </div>
          </div>
          {(result.usd_asset || result.usdz_asset) && (
            <div className="card">
              <label>USD 내보내기 (UsdPreviewSurface)</label>
              <div className="row" style={{ gap: 8, marginTop: 6 }}>
                {result.usd_asset && <button className="ghost" onClick={() => downloadFile(result.usd_asset!.download_url, result.usd_asset!.filename)}>USDA 다운로드</button>}
                {result.usdz_asset && <button className="ghost" onClick={() => downloadFile(result.usdz_asset!.download_url, result.usdz_asset!.filename)}>USDZ 다운로드 (자기완결)</button>}
              </div>
              {result.usdz_asset && <SpinViewer assetId={result.usdz_asset.id} label="🖱 인터랙티브 RTX 뷰어 (Omniverse · 드래그 회전)" />}
            </div>
          )}
        </>
      )}
    </>
  );
}
