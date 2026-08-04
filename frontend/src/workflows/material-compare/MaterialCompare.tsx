"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";
import JobProgress from "../JobProgress";
import Tip from "../Tip";

type Row = {
  part: string;
  material_usd: { key?: string; mdl?: string; subId?: string };
  content_agents?: string | null;
};
type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Result = {
  input: string;
  in_units: string;
  up_axis: string;
  rows: Row[];
  input_cleaned?: boolean;
  clean_meshes?: number;
  content_asset: AssetRec | null;
  content_status: string;
  content_error?: string | null;
  preview_material_usd?: AssetRec | null;
  preview_content?: AssetRec | null;
  render_material_usd?: AssetRec | null;
  render_content?: AssetRec | null;
};

const WF = "material-compare";

export default function MaterialCompare({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [images, setImages] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [srcA, setSrcA] = useState<string | null>(null);
  const [srcB, setSrcB] = useState<string | null>(null);
  const refs = useRef<string[]>([]);
  const acRef = useRef<AbortController | null>(null);

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
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("text", text);
      for (const img of images) fd.append("images", img);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/compare-submit`, fd, { signal: ac.signal });
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
      if (err instanceof CancelledError) setError("취소되었습니다.");
      else setError(String((err as Error).message));
    } finally {
      setBusy(false);
      acRef.current = null;
    }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <label>재질 힌트 (선택, NdotLight 쪽 분류에 사용)<Tip t="재질 추론을 돕는 한 줄 설명. 공정 비교를 위해 NdotLight(material-usd) 엔진에만 전달되고 NVIDIA 쪽엔 주지 않습니다." /></label>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="예: 알루미늄 사다리, 발끝은 고무" />
          <label>참조 이미지 (선택 · <b>NdotLight 쪽에만</b> 전달)<Tip t="실물 사진. NdotLight 엔진이 색·외형으로 재질을 고를 때 참고합니다. NVIDIA는 자체 멀티뷰 렌더를 써서 이 이미지를 받지 않습니다(형상은 양쪽 동일)." /></label>
          <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
          <p className="muted">
            이미지를 넣으면 NdotLight(material-usd)는 실제 색/외형을 보고 vMaterials를 고릅니다.
            NVIDIA content-agents는 자체 멀티뷰 렌더를 쓰므로 이 이미지를 받지 않습니다(공정 비교를 위해 형상은 동일, 힌트만 NdotLight에).
            {images.length > 0 ? ` — 이미지 ${images.length}장 첨부됨` : ""}
          </p>
          <p className="muted">두 엔진을 모두 실행합니다 — <b>수 분</b> 소요(특히 NVIDIA 쪽 WSL 렌더).</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "두 엔진 실행 중…" : "비교 실행"}</button>
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={780} hint="NVIDIA 렌더 포함, 부품 많으면 더" />
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
              <p className="muted" style={{ marginTop: 6 }}>두 엔진이 배정한 재질을 같은 형상 위에 PBR 근사로. 정밀 MDL 룩은 아래 Omniverse 렌더.</p>
            </div>
          )}
          {(result.render_material_usd || result.render_content) && (
            <div className="card">
              <label>인터랙티브 RTX 뷰어 (실제 재질 · 좌우 회전·상하 고도·휠 확대)</label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>material-usd (vMaterials)</div>
                  {result.render_material_usd
                    ? <SpinViewer assetId={result.render_material_usd.id} label="🖱 RTX 뷰어 열기 (드래그·휠)" />
                    : <p className="muted">없음</p>}
                </div>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>NVIDIA content-agents</div>
                  {result.render_content
                    ? <SpinViewer assetId={result.render_content.id} label="🖱 RTX 뷰어 열기 (드래그·휠)" />
                    : <p className="muted">없음</p>}
                </div>
              </div>
              <p className="muted" style={{ marginTop: 6 }}>버튼을 누르면 Isaac RTX로 프레임을 렌더(수십 초~수 분) 후 마우스로 돌려볼 수 있습니다.</p>
            </div>
          )}
          <div className="card">
            <label>부품별 재질 비교 — {result.input} ({result.in_units}, {result.up_axis}-up)</label>
            {result.input_cleaned && (
              <p className="muted" style={{ fontSize: 12 }}>
                ⓘ 공정 비교를 위해 입력을 맨 지오메트리로 정리했습니다 — 거대 환경 평면·baked 재질 제거 + 단위 정규화{result.clean_meshes ? ` · 부품 ${result.clean_meshes}개` : ""}. 두 엔진이 같은 자산에서 재질을 새로 추론합니다.
              </p>
            )}
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
          {(result.render_material_usd || result.content_asset) && (
            <div className="card">
              <label>결과 USD 다운로드</label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 6 }}>
                <div>
                  <div className="muted" style={{ marginBottom: 4 }}>NdotLight (material-usd · vMaterials)</div>
                  {result.render_material_usd
                    ? <button onClick={() => downloadAsset(result.render_material_usd!.download_url, result.render_material_usd!.filename)}>재질 USD 다운로드</button>
                    : <p className="muted">생성 실패/없음</p>}
                </div>
                <div>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="muted">NVIDIA content-agents</span>
                    {result.content_status && <span className={`badge ${result.content_asset ? "ok" : "warn"}`}>{result.content_status}</span>}
                  </div>
                  {result.render_content || result.content_asset ? (
                    <button onClick={() => { const a = result.render_content ?? result.content_asset!; downloadAsset(a.download_url, a.filename); }}>재질 USD 다운로드</button>
                  ) : result.content_error ? (
                    <p className="err" style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>NVIDIA 실패: {result.content_error}</p>
                  ) : <p className="muted">생성 실패/없음</p>}
                </div>
              </div>
              <p className="muted" style={{ marginTop: 6 }}>NdotLight은 자기완결 vMaterials USD(형상+재질), content-agents는 형상 포함 USDZ를 권장합니다.</p>
            </div>
          )}
        </>
      )}
    </>
  );
}
