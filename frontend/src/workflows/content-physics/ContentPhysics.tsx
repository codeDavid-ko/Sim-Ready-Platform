"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, CancelledError, downloadFile, submitAndPoll } from "@/lib/api";
import JobProgress from "../JobProgress";
import type { WorkflowModuleProps } from "../registry";
import Tip from "../Tip";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Phys = {
  mass?: number;
  density?: number;
  static_friction?: number;
  dynamic_friction?: number;
  restitution?: number;
};
type Result = {
  engine: string;
  status: string;
  materials: Record<string, string>;
  physics: Record<string, Phys>;
  asset: AssetRec | null;
  preview?: AssetRec | null;
  usdz_asset?: (AssetRec & { id: string }) | null;
  log_tail: string;
};

const WF = "content-physics";

export default function ContentPhysics({ manifest }: WorkflowModuleProps) {
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

  const fmt = (v?: number) => (v === undefined || v === null ? "—" : v);
  const physRows = result ? Object.entries(result.physics) : [];
  // 물리 dict 에는 (a) 조립체 총질량 (b) 부품별 밀도 (c) 접촉물성 프리셋(PhysMat_*) 이 섞여 있다 → 분리.
  const isMat = (k: string, p: Phys) =>
    k.startsWith("PhysMat") || (p.static_friction != null && p.density == null && p.mass == null);
  const matRows = physRows.filter(([k, p]) => isMat(k, p));
  const totalRow = physRows.find(([k, p]) => !isMat(k, p) && p.mass != null && p.density == null);
  const partRows = physRows.filter(([k, p]) => !isMat(k, p) && !(p.mass != null && p.density == null));

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>USD 파일 ({accept})<Tip t="물리 속성(밀도·질량·마찰 등)을 추론할 USD를 업로드합니다. NVIDIA content-agents의 VLM이 부품별로 값을 추정합니다." /></label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="muted">WSL2에서 멀티뷰 렌더 + 부품 식별 후 질량·마찰·반발을 추론해 <b>UsdPhysics</b>를 적용합니다. <b>수 분</b> 소요.</p>
          <div style={{ marginTop: 12 }}>
            <button type="submit" disabled={busy}>{busy ? "실행 중…" : "물리 추론 실행 (NVIDIA)"}</button>
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={780} hint="멀티뷰 렌더 + 부품별 VLM" />
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>형상 미리보기 (PBR 근사)</label>
              <model-viewer
                src={glbSrc}
                camera-controls
                auto-rotate
                shadow-intensity="1"
                style={{ width: "100%", height: "320px", background: "#0d1117", borderRadius: "8px" }}
              />
            </div>
          )}
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>부품별 물리 속성</label>
              <span className="badge ok">{result.status}</span>
            </div>
            <p className="muted">{result.engine}</p>
            {totalRow && (
              <p style={{ margin: "2px 0 10px" }}>
                조립체 총 질량: <b style={{ fontSize: 16 }}>{fmt(totalRow[1].mass)} kg</b>
              </p>
            )}
            {physRows.length === 0 ? (
              <p className="muted">UsdPhysics 속성이 출력 USD에서 발견되지 않았습니다. (로그 확인)</p>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #e3e6ea" }}>
                    <th style={{ padding: "6px 8px" }}>부품</th>
                    <th style={{ padding: "6px 8px" }}>밀도 (kg/m³)</th>
                    <th style={{ padding: "6px 8px" }}>재질 추정</th>
                  </tr>
                </thead>
                <tbody>
                  {partRows.map(([part, p]) => (
                    <tr key={part} style={{ borderBottom: "1px solid #eef0f2" }}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{part}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.density)}</td>
                      <td style={{ padding: "6px 8px" }}>{result.materials[part] ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {matRows.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <p className="muted" style={{ margin: "0 0 4px" }}>접촉 물성 프리셋 (정지마찰 / 운동마찰 / 반발):</p>
                {matRows.map(([k, p]) => (
                  <p key={k} className="muted" style={{ margin: "1px 0", fontSize: 12.5 }}>
                    • {fmt(p.static_friction)} / {fmt(p.dynamic_friction)} / {fmt(p.restitution)}
                  </p>
                ))}
              </div>
            )}
            <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
              ※ 부품별 질량은 밀도×부피로 PhysX가 계산합니다(USD엔 density로 기록). 마찰·반발은 위 프리셋이 부품에 바인딩됩니다.
            </p>
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 USD (UsdPhysics 적용)</label>
              {result.asset && <span className="badge ok">저장소 등록됨</span>}
            </div>
            <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
              {(result.usdz_asset || result.asset) && (
                <button onClick={() => { const a = result.usdz_asset ?? result.asset!; downloadFile(a.download_url, a.filename); }}>물성 USD 다운로드</button>
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
