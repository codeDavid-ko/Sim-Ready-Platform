"use client";

import { useEffect, useRef, useState } from "react";
import { apiJson, blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = {
  engine: string;
  stl_asset: AssetRec | null;
  preview?: AssetRec | null;
  report: string;
};

const WF = "trinix-model";

export default function TrinixModel({ manifest }: WorkflowModuleProps) {
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
      if (r.preview?.download_url) {
        try { const u = await blobUrl(r.preview.download_url); refs.current.push(u); setGlbSrc(u); } catch { /* */ }
      }
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
          <p className="err">Trinix 가 준비되지 않았습니다 — .env 의 TRINIX_AI_TOKEN 과 라이브 페어링 세션(keep_session.py)이 필요합니다.</p>
        )}
        <form onSubmit={run}>
          <label>설명/요구/치수 (텍스트)</label>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4}
            placeholder="예: 가로 100mm 세로 50mm 높이 30mm 박스, 윗면에 지름 20mm 관통홀 / 또는 도면 설명" />
          <label>참조 이미지 (선택 · 도면/사진, 복수)</label>
          <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
          <p className="muted">구독 Claude가 Trinix CAD를 단계별로 빌드하고 STL로 내보냅니다 — <b>수 분</b> 소요. {images.length > 0 ? `이미지 ${images.length}장 첨부됨.` : ""}</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy || ready === false}>{busy ? "모델링 중… (수 분)" : "3D 모델 생성"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>모델 미리보기 (마우스 회전)</label>
              <model-viewer src={glbSrc} camera-controls auto-rotate shadow-intensity="1"
                style={{ width: "100%", height: "360px", background: "#0d1117", borderRadius: "8px" }} />
              <p className="muted">{result.engine}</p>
            </div>
          )}
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 STL</label>
              {result.stl_asset && <span className="badge ok">저장소 등록됨</span>}
            </div>
            {result.stl_asset ? (
              <>
                <p className="muted">{result.stl_asset.filename} · {(result.stl_asset.bytes / 1024).toFixed(1)} KB</p>
                <div className="row" style={{ marginTop: 8 }}>
                  <button className="ghost" onClick={() => downloadFile(result.stl_asset!.download_url, result.stl_asset!.filename)}>STL 다운로드</button>
                </div>
                <p className="muted" style={{ marginTop: 6 }}>이 STL을 “에셋 준비”나 “물성 추론” 카드에 올리면 sim-ready USD·물성으로 이어갈 수 있습니다.</p>
              </>
            ) : <p className="muted">STL 생성 실패.</p>}
          </div>
          {result.report && (
            <div className="card">
              <label>에이전트 보고 (끝부분)</label>
              <pre>{result.report}</pre>
            </div>
          )}
        </>
      )}
    </>
  );
}
