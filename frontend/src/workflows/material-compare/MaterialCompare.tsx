"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";

type Row = {
  part: string;
  material_usd: { key?: string; mdl?: string; subId?: string };
  content_agents?: string | null;
};
type AssetRec = { filename: string; bytes: number; download_url: string };
type Result = {
  input: string;
  in_units: string;
  up_axis: string;
  rows: Row[];
  content_asset: AssetRec | null;
  content_status: string;
  preview_material_usd?: AssetRec | null;
  preview_content?: AssetRec | null;
};

const WF = "material-compare";

export default function MaterialCompare({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [srcA, setSrcA] = useState<string | null>(null);
  const [srcB, setSrcB] = useState<string | null>(null);
  const refs = useRef<string[]>([]);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    return () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); };
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
      fd.append("text", text);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/compare-submit`, fd);
      setResult(r);
      for (const [rec, set] of [
        [r.preview_material_usd, setSrcA] as const,
        [r.preview_content, setSrcB] as const,
      ]) {
        if (rec?.download_url) {
          try {
            const url = await blobUrl(rec.download_url);
            refs.current.push(url);
            set(url);
          } catch { set(null); }
        }
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
        <form onSubmit={run}>
          <label>USD 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <label>재질 힌트 (선택, material-usd 쪽 분류에 사용)</label>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="예: 알루미늄 사다리, 발끝은 고무" />
          <p className="muted">두 엔진을 모두 실행합니다 — <b>수 분</b> 소요(특히 NVIDIA 쪽 WSL 렌더).</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "두 엔진 실행 중… (수 분)" : "비교 실행"}</button>
          </div>
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {(srcA || srcB) && (
            <div className="card">
              <label>3D 비교 (PBR 근사 · 색/금속성/거칠기)</label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>material-usd (vMaterials)</div>
                  {srcA ? (
                    <model-viewer src={srcA} camera-controls auto-rotate shadow-intensity="1" style={{ width: "100%", height: "280px", background: "#0d1117", borderRadius: "8px" }} />
                  ) : <p className="muted">미리보기 없음</p>}
                </div>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>NVIDIA content-agents</div>
                  {srcB ? (
                    <model-viewer src={srcB} camera-controls auto-rotate shadow-intensity="1" style={{ width: "100%", height: "280px", background: "#0d1117", borderRadius: "8px" }} />
                  ) : <p className="muted">미리보기 없음</p>}
                </div>
              </div>
              <p className="muted" style={{ marginTop: 6 }}>두 엔진이 배정한 재질을 같은 형상 위에 PBR 근사로. 정밀 MDL 룩은 Omniverse/Isaac.</p>
            </div>
          )}
          <div className="card">
            <label>부품별 재질 비교 — {result.input} ({result.in_units}, {result.up_axis}-up)</label>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: "left", borderBottom: "2px solid #e3e6ea" }}>
                  <th style={{ padding: "6px 8px" }}>부품</th>
                  <th style={{ padding: "6px 8px" }}>material-usd (vMaterials)</th>
                  <th style={{ padding: "6px 8px" }}>NVIDIA content-agents</th>
                </tr>
              </thead>
              <tbody>
                {result.rows.map((r) => (
                  <tr key={r.part} style={{ borderBottom: "1px solid #eef0f2" }}>
                    <td style={{ padding: "6px 8px", fontWeight: 600 }}>{r.part}</td>
                    <td style={{ padding: "6px 8px" }}>
                      {r.material_usd?.subId ?? r.material_usd?.key ?? "—"}
                      {r.material_usd?.mdl ? <div className="muted" style={{ fontSize: 11 }}>{r.material_usd.mdl}</div> : null}
                    </td>
                    <td style={{ padding: "6px 8px" }}>{r.content_agents ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted" style={{ marginTop: 8 }}>
              두 엔진은 어휘가 다릅니다 — material-usd는 NVIDIA vMaterials(MDL), content-agents는 자체 재질 라이브러리. 같은 부품에 대한 두 추론을 비교하세요.
            </p>
          </div>
          {result.content_asset && (
            <div className="card">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <label style={{ margin: 0 }}>content-agents 결과 USD</label>
                <span className="badge ok">{result.content_status}</span>
              </div>
              <p className="muted">{result.content_asset.filename} · {(result.content_asset.bytes / 1024).toFixed(1)} KB</p>
              <div className="row" style={{ marginTop: 8 }}>
                <button className="ghost" onClick={() => downloadFile(result.content_asset!.download_url, result.content_asset!.filename)}>USD 다운로드</button>
              </div>
            </div>
          )}
        </>
      )}
    </>
  );
}
