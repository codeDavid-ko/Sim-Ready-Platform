"use client";

import { useRef, useState } from "react";
import { CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import JobProgress from "../JobProgress";
import Tip from "../Tip";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Report = {
  meshes: number;
  materials: string[];
  look_bindings_total: number;
  look_bindings_resolved: number;
  binding_paths_unmatched: string[];
  localized: string[];
  localize_failed: string[];
  missing_textures: string[];
  remapped: number;
  remap_mapping: Record<string, string>;
  self_contained: boolean;
  unresolved: string[];
  usdz_bytes: number;
};
type Result = { asset: AssetRec | null; report: Report };

const WF = "look-merge";
const accept = ".usd,.usda,.usdc,.usdz";

export default function LookMerge({ manifest }: WorkflowModuleProps) {
  const [geo, setGeo] = useState<File | null>(null);
  const [look, setLook] = useState<File | null>(null);
  const [resources, setResources] = useState<File[]>([]);
  const [localize, setLocalize] = useState(true);
  const [remap, setRemap] = useState(true);   // AI 구조 매칭(경로 다를 때 부품 추론 재바인딩)
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asset, setAsset] = useState<AssetRec | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [prog, setProg] = useState<{ phase?: string } | null>(null);
  const acRef = useRef<AbortController | null>(null);

  async function run() {
    if (!geo || !look) { setError("형상 USD와 룩 USD를 모두 선택하세요."); return; }
    setError(null); setBusy(true); setAsset(null); setReport(null); setProg(null);
    const ac = new AbortController(); acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("geometry", geo);
      fd.append("look", look);
      fd.append("localize", String(localize));
      fd.append("remap", String(remap));
      for (const rf of resources) fd.append("resources", rf);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/merge-submit`, fd, { signal: ac.signal, onProgress: setProg });
      setReport(r.report);
      if (r.asset) setAsset(r.asset);
      else setError("결과 파일이 생성되지 않았습니다.");
    } catch (err) {
      setError(err instanceof CancelledError ? "취소되었습니다." : String((err as Error).message));
    } finally {
      setBusy(false); acRef.current = null;
    }
  }

  const mb = (n: number) => (n / 1024 / 1024).toFixed(1);
  const matched = report ? report.look_bindings_resolved : 0;
  const remapped = report ? (report.remapped ?? 0) : 0;
  const total = report ? report.look_bindings_total : 0;
  const applied = matched + remapped;

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>

        <label style={{ marginTop: 8 }}>형상 USD ({accept})<Tip t="당신의 모델 파일. 디자이너가 룩을 만들 때 사용한 바로 그 형상이어야 룩이 제대로 입혀집니다." /></label>
        <input type="file" accept={accept} onChange={(e) => setGeo(e.target.files?.[0] ?? null)} />
        {geo && <p className="muted" style={{ fontSize: 12 }}>· {geo.name} ({mb(geo.size)} MB)</p>}

        <label style={{ marginTop: 12 }}>룩 USD (머티리얼 레이어)<Tip t="디자이너가 준 룩 파일(보통 *_looks.usd). 형상을 sublayer로 참조하거나 머티리얼·바인딩만 들어있습니다." /></label>
        <input type="file" accept={accept} onChange={(e) => setLook(e.target.files?.[0] ?? null)} />
        {look && <p className="muted" style={{ fontSize: 12 }}>· {look.name} ({mb(look.size)} MB)</p>}

        <label style={{ marginTop: 12 }}>룩이 참조하는 텍스처/리소스 (있으면)<Tip t="룩이 로컬 텍스처(예: ./normal.png)를 참조하는데 그 파일이 룩 안에 없을 때, 여기에 같이 올리면 한 파일에 묶입니다. 안 올리면 그 텍스처 참조는 비워지고(머티리얼은 색만 유지) 누락 목록에 표시됩니다." /></label>
        <input type="file" multiple onChange={(e) => setResources(Array.from(e.target.files ?? []))} />
        {resources.length > 0 && <p className="muted" style={{ fontSize: 12 }}>· {resources.length}개: {resources.map((r) => r.name).join(", ")}</p>}

        <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 12 }}>
          <input type="checkbox" checked={localize} onChange={(e) => setLocalize(e.target.checked)} style={{ width: "auto" }} />
          온라인 MDL·텍스처 받아서 한 파일에 번들 (자기완결)<Tip t="룩이 NVIDIA 온라인(https://...mdl) 머티리얼·텍스처를 참조하면, 받아서 ./materials·./textures로 묶어 인터넷 없이도 열리는 자기완결 .usdz로 만듭니다(NVIDIA AA.001 앵커드 경로). 끄면 온라인 참조 그대로 둡니다." />
        </label>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 6 }}>
          <input type="checkbox" checked={remap} onChange={(e) => setRemap(e.target.checked)} style={{ width: "auto" }} />
          AI 구조 매칭 (경로 다를 때 부품 추론)<Tip t="룩의 바인딩 경로(예: /scenes/body)가 형상의 prim 경로(예: /Asset/Geometry_4)와 다르면 그냥은 안 입혀집니다. 켜면 Claude가 부품의 형상(크기·위치)과 이름 의미(body=본체, cap=뚜껑, hinge=핀, front/back=위치)로 룩 부품↔형상 부품을 매칭해 재질을 다시 바인딩합니다. 경로가 이미 맞으면 그대로 적용되고, 이 매칭은 안 맞을 때만 작동합니다." />
        </label>

        <p className="muted" style={{ marginTop: 12, fontSize: 12.5 }}>
          형상+룩을 flatten해 바인딩을 굽고, 텍스처·MDL까지 번들된 단일 <b>.usdz</b>로 내보냅니다.
        </p>
        <div style={{ marginTop: 12 }}>
          <button onClick={run} disabled={busy || !geo || !look}>{busy ? "합치는 중…" : "룩 합쳐 .usdz 만들기"}</button>
        </div>
        <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={30} progress={prog ? { phase: prog.phase } : null} hint="merge+usdz" />
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {report && (
        <div className="card">
          <label style={{ margin: 0 }}>결과</label>
          <ul style={{ margin: "8px 0", paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
            <li>메시 <b>{report.meshes}</b>개 · 머티리얼 <b>{report.materials.length}</b>개</li>
            <li>
              룩 바인딩 적용: <b style={{ color: total > 0 && applied >= total ? "var(--vsc-accent,#2e7d32)" : applied > 0 ? "#b8860b" : "#f48771" }}>{applied} / {total}</b>
              {remapped > 0 && <span className="muted" style={{ fontSize: 12 }}> (경로일치 {matched} + AI 매칭 {remapped})</span>}
              {total > 0 && applied === 0 && " — ⚠ 안 입혀짐(경로 불일치 + AI 매칭도 실패)"}
              {total > 0 && applied > 0 && applied < total && " — 일부만 적용"}
            </li>
            {remapped > 0 && (
              <li className="muted" style={{ fontSize: 12 }}>
                🤖 AI 매칭: {Object.entries(report.remap_mapping).map(([k, v]) => `${k}→${v}`).join(", ")}
                <span style={{ color: "#b8860b" }}> — 매칭이 어색하면 확인 필요(특히 앞/뒤 같은 동일형상 쌍)</span>
              </li>
            )}
            {report.localized.length > 0 && (
              <li>로컬 번들: {report.localized.join(", ")}</li>
            )}
            {report.localize_failed.length > 0 && (
              <li className="err" style={{ fontSize: 12 }}>받기 실패: {report.localize_failed.join(", ")}</li>
            )}
            {report.missing_textures.length > 0 && (
              <li style={{ color: "#b8860b", fontSize: 12.5 }}>
                누락 텍스처(참조 비움) {report.missing_textures.length}개: {report.missing_textures.join(", ")} — 디자이너에게 받아 위 “텍스처/리소스”에 같이 올리면 묶입니다.
              </li>
            )}
            <li>
              자기완결: <b style={{ color: report.self_contained ? "var(--vsc-accent,#2e7d32)" : "#f48771" }}>{report.self_contained ? "예 (외부 의존 없음)" : "아니오"}</b>
              {!report.self_contained && report.unresolved.length > 0 && (
                <span className="muted" style={{ fontSize: 12 }}> — 미해결: {report.unresolved.join(", ")}</span>
              )}
            </li>
          </ul>

          {total > 0 && applied === 0 && (
            <p className="muted" style={{ fontSize: 12.5, padding: "8px 12px", borderRadius: 6, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>
              ⓘ 룩 바인딩이 <code>{report.binding_paths_unmatched[0]}</code> 같은 경로를 가리키는데 이 형상엔 그 prim이 없고, AI 매칭도 대응을 못 찾았습니다.
              <b>AI 구조 매칭</b>을 켜고 다시 시도하거나, 디자이너가 룩을 만들 때 쓴 형상(또는 부품 형상이 비슷한 형상)을 올려보세요.
            </p>
          )}

          {asset && (
            <div className="row" style={{ marginTop: 8 }}>
              <button onClick={() => downloadAsset(asset.download_url, asset.filename)}>
                .usdz 다운로드 ({mb(asset.bytes)} MB)
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
