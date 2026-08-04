"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, CancelledError, downloadFile, submitAndPoll } from "@/lib/api";
import JobProgress from "../JobProgress";
import type { WorkflowModuleProps } from "../registry";
import Tip from "../Tip";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = {
  engine: string;
  status: string;
  materials: Record<string, string>;
  asset: AssetRec | null;
  preview?: AssetRec | null;
  usdz_asset?: (AssetRec & { id: string }) | null;
  log_tail: string;
};

const WF = "content-texture";

export default function ContentTexture({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);
  const glbRef = useRef<string | null>(null);
  const acRef = useRef<AbortController | null>(null);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    return () => {
      if (glbRef.current) URL.revokeObjectURL(glbRef.current);
    };
  }, []);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    if (!file) {
      setError("USD 파일을 선택하세요.");
      return;
    }
    setBusy(true);
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("file", file);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd, { signal: ac.signal });
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
      setError(err instanceof CancelledError ? "취소되었습니다." : String((err as Error).message));
    } finally {
      setBusy(false);
      acRef.current = null;
    }
  }

  const matRows = result ? Object.entries(result.materials) : [];

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})<Tip t="텍스처/재질을 추론할 USD를 업로드합니다. NVIDIA content-agents가 부품별로 재질·텍스처를 배정합니다." /></label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="muted">WSL2에서 멀티뷰 렌더 + 부품별 구독 Claude VLM으로 재질/텍스처를 추론합니다. 텍스처 <b>생성</b>은 NVIDIA_API_KEY가 있을 때만. <b>수 분</b> 소요.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "실행 중…" : "텍스처 추론 실행 (NVIDIA)"}</button>
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={780} hint="멀티뷰 렌더 + 부품별 VLM" />
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>결과 미리보기 (PBR 근사 · 색/금속성/거칠기)</label>
              <model-viewer
                src={glbSrc}
                camera-controls
                auto-rotate
                shadow-intensity="1"
                style={{ width: "100%", height: "320px", background: "#0d1117", borderRadius: "8px" }}
              />
              <p className="muted">정밀 룩은 Omniverse/Isaac에서, 여기선 PBR 근사입니다.</p>
            </div>
          )}
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>부품별 추론 재질/텍스처</label>
              <span className="badge ok">{result.status}</span>
            </div>
            <p className="muted">{result.engine}</p>
            {matRows.length === 0 ? (
              <p className="muted">재질 바인딩이 출력 USD에서 발견되지 않았습니다. (로그 확인)</p>
            ) : (
              matRows.map(([part, mat]) => (
                <div className="check" key={part}>
                  <span className="mark">▢</span>
                  <span><b>{part}</b> → {mat}</span>
                </div>
              ))
            )}
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 USD</label>
              {result.asset && <span className="badge ok">저장소 등록됨</span>}
            </div>
            <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
              {(result.usdz_asset || result.asset) && (
                <button onClick={() => { const a = result.usdz_asset ?? result.asset!; downloadFile(a.download_url, a.filename); }}>재질/텍스처 USD 다운로드</button>
              )}
            </div>
          </div>
          {result.usdz_asset && (
            <div className="card">
              <label style={{ margin: 0 }}>인터랙티브 RTX 뷰어 (좌우 회전·상하 고도·휠 확대)</label>
              <p className="muted">결과를 USDZ로 묶어 Isaac Sim RTX로 렌더 후 마우스로 돌려봅니다.</p>
              <SpinViewer assetId={result.usdz_asset.id} label="🖱 RTX 뷰어 열기 (드래그·휠)" />
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
