"use client";

import { useEffect, useRef, useState } from "react";
import { apiJson, blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Shape = { name: string; size_mm: number[]; center_m: number[] };
type Result = {
  engine: string;
  shots: string[];
  shape_count: number;
  shapes: Shape[];
  proxy_usd: AssetRec | null;
  proxy_stl: AssetRec | null;
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
  const [shotUrls, setShotUrls] = useState<string[]>([]);
  const refs = useRef<string[]>([]);

  useEffect(() => {
    apiJson<{ ready: boolean }>(`/api/workflows/${WF}/ready`).then((r) => setReady(r.ready)).catch(() => setReady(null));
    return () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); };
  }, []);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null); setResult(null); setShotUrls([]);
    if (!text.trim() && images.length === 0) { setError("텍스트 설명 또는 참조 이미지를 입력하세요."); return; }
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("text", text.trim());
      for (const img of images) fd.append("images", img);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd);
      setResult(r);
      const urls: string[] = [];
      for (const s of r.shots) { try { const u = await blobUrl(s); refs.current.push(u); urls.push(u); } catch { /* */ } }
      setShotUrls(urls);
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
          <p className="err">Trinix 가 준비되지 않았습니다 — .env 의 TRINIX_AI_TOKEN + 브라우저에서 에디터 열고 우하단 MCP 점이 녹색이어야 합니다.</p>
        )}
        <form onSubmit={run}>
          <label>설명/요구/치수 (텍스트)</label>
          <textarea value={text} onChange={(e) => setText(e.target.value)} rows={4}
            placeholder="예: 가로 100mm 세로 50mm 높이 30mm 박스, 윗면에 지름 20mm 관통홀" />
          <label>참조 이미지 (선택 · 도면/사진, 복수)</label>
          <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
          <p className="muted">구독 Claude가 Trinix CAD를 빌드하고 여러 각도로 캡처합니다 — <b>수 분</b> 소요. {images.length > 0 ? `이미지 ${images.length}장 첨부됨.` : ""}</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy || ready === false}>{busy ? "모델링 중… (수 분)" : "3D 모델 생성 + 스크린샷"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {shotUrls.length > 0 ? (
            <div className="card">
              <label>Trinix 스크린샷 ({shotUrls.length}장)</label>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 8 }}>
                {shotUrls.map((u, i) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={i} src={u} alt={`shot ${i}`} style={{ width: "100%", borderRadius: 6, background: "#0d1117" }} />
                ))}
              </div>
              <p className="muted" style={{ marginTop: 6 }}>{result.engine}</p>
            </div>
          ) : <div className="card"><p className="muted">스크린샷을 받지 못했습니다(에디터 연결 확인).</p></div>}

          {result.shapes.length > 0 && (
            <div className="card">
              <label>부품 ({result.shape_count})</label>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead><tr style={{ textAlign: "left", borderBottom: "2px solid #3c3c3c" }}>
                  <th style={{ padding: "5px 8px" }}>이름</th><th style={{ padding: "5px 8px" }}>크기(mm)</th></tr></thead>
                <tbody>
                  {result.shapes.map((sh, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #2d2d30" }}>
                      <td style={{ padding: "5px 8px", fontWeight: 600 }}>{sh.name}</td>
                      <td style={{ padding: "5px 8px" }}>{sh.size_mm.join(" × ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(result.proxy_usd || result.proxy_stl) && (
            <div className="card">
              <label>bbox 프록시 내보내기</label>
              <p className="muted">정밀 형상이 아니라 <b>부품별 바운딩박스</b>(블록) — 부품수·위치·크기는 정확. Trinix 정밀 export(export_scene) 복구 시 실제 형상으로 교체됩니다.</p>
              <div className="row" style={{ marginTop: 8, gap: 8 }}>
                {result.proxy_usd && <button className="ghost" onClick={() => downloadFile(result.proxy_usd!.download_url, result.proxy_usd!.filename)}>프록시 USD</button>}
                {result.proxy_stl && <button className="ghost" onClick={() => downloadFile(result.proxy_stl!.download_url, result.proxy_stl!.filename)}>프록시 STL</button>}
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
