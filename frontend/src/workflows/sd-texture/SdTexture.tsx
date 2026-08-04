"use client";

import { useEffect, useRef, useState } from "react";
import { apiJson, blobUrl, CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import JobProgress from "../JobProgress";
import SpinViewer from "../SpinViewer";
import Tip from "../Tip";
import NumberInput from "../NumberInput";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = {
  engine: string;
  mode?: string;
  prompt: string;
  strength?: number | null;
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
  const [image, setImage] = useState<File | null>(null);
  const [imgPreview, setImgPreview] = useState<string | null>(null);
  const [strength, setStrength] = useState(0.55);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [ready, setReady] = useState<boolean | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);
  const [thumbs, setThumbs] = useState<{ albedo?: string; normal?: string; roughness?: string }>({});
  const refs = useRef<string[]>([]);
  const acRef = useRef<AbortController | null>(null);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    apiJson<{ ready: boolean }>(`/api/workflows/${WF}/ready`).then((r) => setReady(r.ready)).catch(() => setReady(null));
    return () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); };
  }, []);

  function pickImage(f: File | null) {
    setImgPreview((prev) => { if (prev) URL.revokeObjectURL(prev); return f ? URL.createObjectURL(f) : null; });
    setImage(f);
  }

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    setGlbSrc(null);
    setThumbs({});
    if (!prompt.trim() && !image) { setError("프롬프트를 입력하거나 참조 이미지를 올리세요."); return; }
    setBusy(true);
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("prompt", prompt.trim());
      fd.append("size", String(size));
      fd.append("steps", String(steps));
      fd.append("seed", String(seed));
      if (image) {
        fd.append("image", image);
        fd.append("strength", String(strength));
      }
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd, { signal: ac.signal });
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
        {ready === false && (
          <p className="err">로컬 SD 환경(~/sd_texture_venv)이 아직 준비되지 않았습니다. 설치 완료 후 사용 가능합니다.</p>
        )}
        <form onSubmit={run}>
          <label>프롬프트 (영어 권장 — SD는 영어로 학습됨){image && " · 선택"}<Tip t="생성할 머티리얼을 묘사하는 문장. 재질·상태·색을 구체적으로 쓸수록 좋습니다(예: rusted brushed steel, scratches). SD가 영어로 학습돼 영어 권장. 참조 이미지를 올리면 프롬프트는 비워도 됩니다." /></label>
          <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3}
            placeholder="e.g. rusted brushed steel plate, weathered, scratches" />

          <label style={{ marginTop: 10 }}>참조 이미지 (선택 · img2img)<Tip t="이미지를 올리면 그 느낌을 따라 텍스처를 생성합니다(img2img). 프롬프트와 함께 주면 둘을 섞습니다. 안 올리면 프롬프트만으로 생성(text2img)." /></label>
          <div className="row" style={{ gap: 12, alignItems: "flex-start", flexWrap: "wrap" }}>
            <input type="file" accept="image/*" onChange={(e) => pickImage(e.target.files?.[0] ?? null)} />
            {imgPreview && (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={imgPreview} alt="reference" style={{ width: 64, height: 64, objectFit: "cover", borderRadius: 8, border: "1px solid var(--vsc-border)" }} />
                <button type="button" className="ghost" onClick={() => pickImage(null)}>이미지 제거</button>
              </div>
            )}
          </div>
          {image && (
            <div style={{ marginTop: 8 }}>
              <label>변형 강도 (strength {strength.toFixed(2)})<Tip t="img2img 변형 정도. 낮으면 원본 이미지를 더 보존, 높으면 프롬프트·SD 해석을 더 반영. 0.4~0.7 권장." /></label>
              <input type="range" min={0.1} max={0.95} step={0.05} value={strength}
                onChange={(e) => setStrength(Number(e.target.value))} style={{ width: 260, display: "block" }} />
            </div>
          )}
          <div className="row" style={{ gap: 16, marginTop: 8, flexWrap: "wrap" }}>
            <div><label>해상도<Tip t="생성되는 텍스처 맵의 픽셀 크기. 클수록 디테일↑·생성 느림·VRAM↑." /></label>
              <select value={size} onChange={(e) => setSize(Number(e.target.value))}>
                <option value={512}>512</option><option value={768}>768</option><option value={1024}>1024</option>
              </select></div>
            <div><label>스텝 (SD-Turbo: 1~4)<Tip t="확산(denoise) 반복 횟수. 많을수록 품질↑·느림. SD-Turbo는 1~4면 충분." /></label>
              <NumberInput min={1} max={8} value={steps} onChange={(n) => setSteps(n)} style={{ width: 90 }} /></div>
            <div><label>시드<Tip t="난수 시드. 같은 프롬프트+시드면 같은 결과가 재현됩니다. 0이면 매번 다른 결과(랜덤)." /></label>
              <NumberInput value={seed} onChange={(n) => setSeed(n)} style={{ width: 120 }} /></div>
          </div>
          <p className="muted">로컬 GPU에서 무료로 생성됩니다(외부 키 불필요). 첫 실행은 모델 다운로드로 몇 분 더 걸릴 수 있습니다.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy || ready === false}>{busy ? "생성 중…" : "텍스처 생성"}</button>
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={30} />
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
            <label>PBR 텍스처 맵{result.mode === "img2img" ? ` · img2img (strength ${(result.strength ?? 0).toFixed(2)})` : " · text2img"}</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
              {(["albedo", "normal", "roughness"] as const).map((k) => (
                <div key={k}>
                  <div className="muted" style={{ marginBottom: 4 }}>{k}</div>
                  {thumbs[k] ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={thumbs[k]} alt={k} style={{ width: "100%", borderRadius: 6, background: "#0d1117", imageRendering: "auto" }} />
                  ) : <p className="muted">—</p>}
                  {result.maps[k] && (
                    <button className="ghost" style={{ marginTop: 6 }} onClick={() => downloadAsset(result.maps[k]!.download_url, result.maps[k]!.filename)}>PNG</button>
                  )}
                </div>
              ))}
            </div>
          </div>
          {(result.usd_asset || result.usdz_asset) && (
            <div className="card">
              <label>USD 내보내기 (UsdPreviewSurface)</label>
              <div className="row" style={{ gap: 8, marginTop: 6 }}>
                {result.usd_asset && <button className="ghost" onClick={() => downloadAsset(result.usd_asset!.download_url, result.usd_asset!.filename)}>USDA 다운로드</button>}
                {result.usdz_asset && <button className="ghost" onClick={() => downloadAsset(result.usdz_asset!.download_url, result.usdz_asset!.filename)}>USDZ 다운로드 (자기완결)</button>}
              </div>
              {result.usdz_asset && <SpinViewer assetId={result.usdz_asset.id} label="🖱 인터랙티브 RTX 뷰어 (Omniverse · 드래그 회전)" />}
            </div>
          )}
        </>
      )}
    </>
  );
}
