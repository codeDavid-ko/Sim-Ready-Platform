"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, blobUrl, downloadFile } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = {
  engine: string;
  status: string;
  bindings: Record<string, string>;
  asset: AssetRec | null;
  preview?: AssetRec | null;
  log_tail: string;
};

const WF = "content-material";

export default function ContentMaterial({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);
  const glbRef = useRef<string | null>(null);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    return () => { if (glbRef.current) URL.revokeObjectURL(glbRef.current); };
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
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/workflows/${WF}/run`, {
        method: "POST",
        headers: authHeaders(),
        body: fd,
      });
      if (!res.ok) {
        let d = `오류 (${res.status})`;
        try { d = (await res.json()).detail ?? d; } catch {}
        setError(d);
        return;
      }
      const r = (await res.json()).result as Result;
      setResult(r);
      if (r.preview?.download_url) {
        try {
          const url = await blobUrl(r.preview.download_url);
          if (glbRef.current) URL.revokeObjectURL(glbRef.current);
          glbRef.current = url;
          setGlbSrc(url);
        } catch { setGlbSrc(null); }
      }
    } catch {
      setError("백엔드에 연결할 수 없습니다(또는 시간 초과).");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="muted">WSL2에서 멀티뷰 렌더 + 부품별 구독 Claude VLM 분류가 돌아 <b>수 분</b> 걸립니다.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "실행 중… (수 분)" : "재질 추론 실행 (NVIDIA)"}</button>
          </div>
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
              <p className="muted">정밀 MDL 룩은 Omniverse/Isaac에서, 여기선 PBR 근사입니다.</p>
            </div>
          )}
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>부품별 추론 재질</label>
              <span className="badge ok">{result.status}</span>
            </div>
            <p className="muted">{result.engine}</p>
            {Object.entries(result.bindings).map(([part, mat]) => (
              <div className="check" key={part}>
                <span className="mark">▢</span>
                <span><b>{part}</b> → {mat}</span>
              </div>
            ))}
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 USD</label>
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
