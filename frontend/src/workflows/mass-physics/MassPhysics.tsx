"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, blobUrl, CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import JobProgress from "../JobProgress";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";
import Tip from "../Tip";
import NumberInput from "../NumberInput";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Part = {
  name: string;
  material?: string;
  bound_visual_material?: string;
  confidence?: number;
  density?: number;
  volume_m3: number;
  area_m2?: number;
  mass_kg?: number;
  solid_mass_kg?: number;
  auto_wall_mm?: number;
  shell_eligible?: boolean;
  shell_reason?: string;
  dims_mm: number[];
  volume_method: string;
  static_friction?: number;
  dynamic_friction?: number;
  restitution?: number;
  material_reasoning?: string;
  physics_reasoning?: string;
};
type Result = {
  engine: string;
  in_units: string;
  layout: string;
  llm_used: boolean;
  part_count: number;
  total_mass_kg: number;
  solid_total_mass_kg?: number;
  auto_shell_applied?: number[];
  parts: Part[];
  clamped: { part_index: number; name: string; param: string }[];
  validation: { ran: boolean; violations: string[]; error?: string };
  asset: AssetRec | null;
  preview?: AssetRec | null;
};

const WF = "mass-physics";

export default function MassPhysics({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".stl,.step,.stp,.obj,.ply,.glb";
  const [file, setFile] = useState<File | null>(null);
  const [units, setUnits] = useState("mm");
  const [context, setContext] = useState("");
  const [layout, setLayout] = useState("assembled");
  const [preserve, setPreserve] = useState(false);  // 구조 보존(경로 유지) — USD 입력 위에 물리만 얹음
  const [collision, setCollision] = useState("convexHull");  // 충돌 전략 (convexHull/convexDecomposition/sdf)
  const [shellMode, setShellMode] = useState<"auto" | "solid" | "manual">("auto"); // 쉘 두께 모드
  const [shellMm, setShellMm] = useState(3);   // 수동 두께(mm) — manual 모드일 때만
  const [dling, setDling] = useState(false);   // 다운로드(재작성) 진행중
  const [images, setImages] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [inferMs, setInferMs] = useState<number | null>(null);  // 마지막 추론 소요시간(ms)
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
    setInferMs(null);
    const t0 = Date.now();
    if (!file) {
      setError("STEP/STL 파일을 선택하세요.");
      return;
    }
    setBusy(true);
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("in_units", units);
      fd.append("context", context);
      fd.append("layout", layout);
      fd.append("preserve", String(preserve));
      fd.append("collision", collision);
      for (const img of images) fd.append("images", img);
      const r = await submitAndPoll<Result>(`/api/workflows/${WF}/submit`, fd, { signal: ac.signal });
      setResult(r);
      setShellMode("auto");   // 결과는 기본 AI 자동 두께로 작성돼 있음
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
      setInferMs(Date.now() - t0);
    }
  }

  const fmt = (v?: number) => (v === undefined || v === null ? "—" : v);
  const round2 = (v?: number) => (v === undefined || v === null ? "—" : Math.round(v * 1000) / 1000);
  const vOk = result?.validation?.ran && result.validation.violations.length === 0;

  // 쉘 두께 미리보기 — 질량 = 밀도 × 표면적 × 두께(정비례). 솔리드 기준값에서 즉시 계산(백엔드 reauthor 와 동일식).
  // 적격(비폐쇄)만 보정, 솔리드 질량 상한. mode: auto=부품별 AI두께, solid=보정안함, manual=전부 같은 두께.
  function shellMass(p: Part): { mass: number; preview: boolean } {
    const solid = p.solid_mass_kg ?? p.mass_kg ?? 0;
    if (!p.shell_eligible || !p.area_m2 || !p.density) return { mass: solid, preview: false };
    const t = shellMode === "solid" ? 0 : shellMode === "manual" ? shellMm : (p.auto_wall_mm ?? 0);
    if (!t || t <= 0) return { mass: solid, preview: false };
    const m = Math.min(p.density * p.area_m2 * (t / 1000), solid);
    return { mass: m, preview: true };
  }
  const disp = result ? result.parts.map(shellMass) : [];
  const dispTotal = disp.reduce((s, d) => s + (d.mass ?? 0), 0);
  const anyEligible = !!result?.parts.some((p) => p.shell_eligible);
  const partThick = (p: Part) =>
    shellMode === "solid" ? 0 : shellMode === "manual" ? shellMm : (p.auto_wall_mm ?? 0);
  const shellMmParam = shellMode === "auto" ? -1 : shellMode === "solid" ? 0 : shellMm;

  // 다운로드: 현재 모드/두께로 USD 를 그 자리에서 재작성(reauthor) 후 받기 — LLM·메시 재계산 없음.
  async function downloadShelled() {
    if (!result?.asset) return;
    setDling(true); setError(null);
    try {
      const fd = new FormData();
      fd.append("asset_id", result.asset.id);
      fd.append("shell_mm", String(shellMmParam));
      const res = await fetch(`${API_BASE}/api/workflows/${WF}/shell-reauthor`, { method: "POST", headers: authHeaders(), body: fd });
      if (!res.ok) throw new Error(`reauthor ${res.status}`);
      const r = (await res.json()) as { asset: AssetRec };
      await downloadAsset(r.asset.download_url, r.asset.filename);
    } catch (err) {
      setError(`USD 생성 실패: ${String((err as Error).message)}`);
    } finally { setDling(false); }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <form onSubmit={run}>
          <label>STEP/STL 파일 ({accept})</label>
          <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <div className="row" style={{ gap: 16, marginTop: 8 }}>
            <div>
              <label>입력 단위<Tip t="업로드한 형상의 좌표 단위. 부피·질량 계산의 기준이라 틀리면 질량이 크게 어긋납니다(STL/OBJ는 단위 정보가 없어 직접 지정 필요)." /></label>
              <select value={units} onChange={(e) => setUnits(e.target.value)}>
                <option value="mm">mm</option>
                <option value="m">m</option>
                <option value="cm">cm</option>
                <option value="in">inch</option>
              </select>
            </div>
            <div>
              <label>레이아웃<Tip t="결과 USD에서 부품 배치. assembled=원래 좌표 그대로. droptest=부품을 띄워 중력으로 떨어뜨려 안착(물리 검증용)." /></label>
              <select value={layout} onChange={(e) => setLayout(e.target.value)}>
                <option value="assembled">assembled (원좌표)</option>
                <option value="droptest">droptest (낙하·정착)</option>
              </select>
            </div>
            <div>
              <label>충돌 전략<Tip t="충돌체 근사 방식. convexHull=볼록껍질(빠르고 안정·오목X, 기본). convexDecomposition=오목 형상 보존·안정(PhysX 분해). sdf=정확·오목O이지만 얇은 고폴리 메시는 폭발 위험(NVIDIA Prop-Robotics-Physx 요구). 단순/정적 프롭은 convexHull, 로봇 납품(sdf 필요)은 깨끗한 메시에 sdf." /></label>
              <select value={collision} onChange={(e) => setCollision(e.target.value)}>
                <option value="convexHull">convexHull (기본·안정)</option>
                <option value="convexDecomposition">convexDecomposition (오목·안정)</option>
                <option value="sdf">sdf (정확·NVIDIA Physx용)</option>
              </select>
            </div>
          </div>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 8 }}>
            <input type="checkbox" checked={preserve} onChange={(e) => setPreserve(e.target.checked)} style={{ width: "auto" }} />
            구조 보존 (경로·재질 유지)<Tip t="USD 입력일 때만 적용. 켜면 입력의 prim 경로·계층·시각 재질(룩)을 그대로 두고 물리(강체·정확한 질량·충돌·물리머티리얼)만 제자리에 얹어 자기완결로 내보냅니다. 룩 합치기 결과처럼 경로가 중요한(예: /scenes 바인딩) 자산에 쓰세요. 끄면(기본) 형상을 /World 아래로 새로 정리(STL/STEP·구조 무관 자산용)." />
          </label>
          <p className="muted" style={{ marginTop: 6 }}>속 빈 형상(프레임·외함·판금)의 질량 과대 보정은 <b>실행 후 결과 표에서</b> AI 자동/슬라이더로 조정합니다.</p>
          <label style={{ marginTop: 8 }}>제품/맥락 힌트 (선택 — Stage1 재질분류 prior)<Tip t="이 물건이 무엇이고 무슨 재질인지 한 줄 힌트. 재질 분류 LLM이 참고해 더 맞는 재질을 고릅니다(예: 폴리프로필렌 박스)." /></label>
          <input value={context} onChange={(e) => setContext(e.target.value)} placeholder="예: 접이식 운반 박스(폴리프로필렌), 또는 산업용 강철 브래킷" />
          <label style={{ marginTop: 8 }}>참조 이미지 (선택 · 복수 — 실제 색/외형으로 재질 추론 보강)<Tip t="실물 사진(여러 장). 색·표면 질감으로 재질 분류 정확도를 높입니다. 형상엔 영향 없고 재질 추론에만 쓰입니다." /></label>
          <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
          {images.length > 0 && <p className="muted">이미지 {images.length}장 첨부됨 — Stage1 재질 분류에 사용됩니다.</p>}
          <p className="muted">질량·부피·관성은 형상에서 정확 계산(LLM 아님). 재질·접촉계수만 구독 Claude가 추론합니다.</p>
          <div style={{ marginTop: 12, display: "flex", gap: 10, alignItems: "center" }}>
            <button type="submit" disabled={busy}>{busy ? "추론 중…" : "물성 추론 실행"}</button>
            {inferMs != null && !busy && <span className="muted" style={{ fontSize: 12 }}>Inference time: {(inferMs / 1000).toFixed(1)}s</span>}
          </div>
          <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} etaSec={45} />
        </form>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {result && (
        <>
          {glbSrc && (
            <div className="card">
              <label>형상 미리보기</label>
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
              <label style={{ margin: 0 }}>물성 결과 — {result.part_count}개 부품 · 총 질량 <b style={{ color: "var(--vsc-accent,#2e7d32)" }}>{round2(dispTotal)} kg</b>{result.solid_total_mass_kg && Math.abs(result.solid_total_mass_kg - dispTotal) > 0.5 ? <span className="muted" style={{ fontSize: 11 }}> (솔리드 가정 시 {result.solid_total_mass_kg} kg)</span> : null}</label>
              <span className={`badge ${vOk ? "ok" : ""}`}>{vOk ? "usdchecker 통과" : (result.validation.ran ? `위반 ${result.validation.violations.length}` : "검증 미실행")}</span>
            </div>
            <p className="muted">{result.engine}{result.llm_used ? "" : " · ⚠ 인증 없음: 재질 steel 기본값"}</p>

            {anyEligible && (
              <div style={{ padding: "8px 10px", margin: "6px 0", background: "var(--vsc-bg-subtle,#f3f6f4)", borderRadius: 6 }}>
                <div className="row" style={{ alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <span style={{ fontSize: 12.5, fontWeight: 600 }}>🧪 속 빈 형상 질량 보정</span>
                  <label style={{ margin: 0, fontSize: 12.5 }}><input type="radio" checked={shellMode === "auto"} onChange={() => setShellMode("auto")} /> AI 자동(부품별 벽두께)</label>
                  <label style={{ margin: 0, fontSize: 12.5 }}><input type="radio" checked={shellMode === "manual"} onChange={() => setShellMode("manual")} /> 직접 지정</label>
                  <label style={{ margin: 0, fontSize: 12.5 }}><input type="radio" checked={shellMode === "solid"} onChange={() => setShellMode("solid")} /> 솔리드(보정 안 함)</label>
                  {shellMode === "manual" && (
                    <span className="row" style={{ alignItems: "center", gap: 6 }}>
                      <input type="range" min={0.5} max={10} step={0.5} value={shellMm} onChange={(e) => setShellMm(Number(e.target.value))} style={{ width: 120 }} />
                      <NumberInput min={0} max={50} step={0.5} value={shellMm} onChange={(n) => setShellMm(n)} style={{ width: 60 }} /> mm
                    </span>
                  )}
                </div>
                <p className="muted" style={{ fontSize: 11, margin: "4px 0 0" }}>
                  {shellMode === "auto" ? "LLM이 총질량을 보고 부품마다 현실적인 벽두께를 정해 보정한 값입니다. 표의 질량·다운로드 USD가 즉시 반영됩니다."
                    : shellMode === "manual" ? "비폐쇄(과대) 부품 전부에 같은 벽두께를 적용해 미리봅니다. 슬라이더만 움직이면 표·다운로드가 즉시 바뀝니다."
                    : "보정 없이 솔리드(꽉 찬) 가정 그대로의 질량입니다."}
                </p>
              </div>
            )}
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                <thead>
                  <tr style={{ textAlign: "left", borderBottom: "2px solid #e3e6ea" }}>
                    <th style={{ padding: "6px 8px" }}>부품</th>
                    <th style={{ padding: "6px 8px" }}>재질</th>
                    <th style={{ padding: "6px 8px" }}>밀도</th>
                    <th style={{ padding: "6px 8px" }}>부피 (m³)</th>
                    <th style={{ padding: "6px 8px" }}>질량 (kg)</th>
                    <th style={{ padding: "6px 8px" }}>μs</th>
                    <th style={{ padding: "6px 8px" }}>μd</th>
                    <th style={{ padding: "6px 8px" }}>e</th>
                    <th style={{ padding: "6px 8px" }}>부피법</th>
                    <th style={{ padding: "6px 8px" }}>AI 선정 이유 (재질 / 물성)</th>
                  </tr>
                </thead>
                <tbody>
                  {result.parts.map((p, i) => (
                    <tr key={i} style={{ borderBottom: "1px solid #eef0f2" }}>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>{p.name}</td>
                      <td style={{ padding: "6px 8px" }}>{p.material ?? "—"}{p.bound_visual_material ? <span className="muted" style={{ fontSize: 10 }}> ←{p.bound_visual_material}</span> : null}{p.confidence !== undefined ? <span className="muted" style={{ fontSize: 10 }}> ({Math.round((p.confidence ?? 0) * 100)}%)</span> : null}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.density)}</td>
                      <td style={{ padding: "6px 8px" }}>{p.volume_m3?.toExponential(2)}</td>
                      <td style={{ padding: "6px 8px", fontWeight: 600 }}>
                        {disp[i]?.preview
                          ? <span title={`솔리드 ${p.solid_mass_kg} kg → 쉘 ${partThick(p)}mm`} style={{ color: "var(--vsc-accent,#2e7d32)" }}>{round2(disp[i].mass)}</span>
                          : round2(disp[i]?.mass ?? p.mass_kg)}
                      </td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.static_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.dynamic_friction)}</td>
                      <td style={{ padding: "6px 8px" }}>{fmt(p.restitution)}</td>
                      <td style={{ padding: "6px 8px" }}>
                        <span className="muted" style={{ fontSize: 11 }}>{disp[i]?.preview ? `shell(${partThick(p)}mm)` : p.volume_method}</span>
                      </td>
                      <td style={{ padding: "6px 8px", minWidth: 240, maxWidth: 360 }} className="muted">
                        {p.material_reasoning ? <div>🎨 {p.material_reasoning}</div> : null}
                        {p.physics_reasoning ? <div>⚙ {p.physics_reasoning}</div> : null}
                        {p.shell_reason ? <div>🧱 {p.shell_reason}{p.auto_wall_mm ? ` (AI ${p.auto_wall_mm}mm)` : ""}</div> : null}
                        {!p.material_reasoning && !p.physics_reasoning && !p.shell_reason ? "—" : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr style={{ borderTop: "2px solid #e3e6ea", fontWeight: 700 }}>
                    <td style={{ padding: "6px 8px" }} colSpan={4}>합계 ({result.part_count}개 부품)</td>
                    <td style={{ padding: "6px 8px", color: "var(--vsc-accent,#2e7d32)" }}>{round2(dispTotal)} kg{disp.some((d) => d.preview) && result.solid_total_mass_kg ? <span className="muted" style={{ fontSize: 10, fontWeight: 400 }}> (솔리드 {result.solid_total_mass_kg})</span> : null}</td>
                    <td colSpan={5}></td>
                  </tr>
                </tfoot>
              </table>
            </div>
            {result.clamped.length > 0 && (
              <p className="muted" style={{ marginTop: 6 }}>접촉계수 clamp: {result.clamped.map((c) => `${c.name}.${c.param}`).join(", ")}</p>
            )}
            <p className="muted" style={{ marginTop: 6 }}>μs=정지마찰, μd=운동마찰, e=반발. 행에 커서를 올리면 추론 근거가 보입니다. <b>mass=density×volume</b>(형상 정확값).</p>
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>결과 USD (UsdPhysics · {result.layout})</label>
              {result.asset && <span className="badge ok">저장소 등록됨</span>}
            </div>
            {result.asset && (
              <>
                <p className="muted">{result.asset.filename} · metersPerUnit=1, Z-up, 중력 9.81</p>
                <div className="row" style={{ marginTop: 8, gap: 8, alignItems: "center" }}>
                  <button onClick={downloadShelled} disabled={dling}>
                    {dling ? "생성 중…" : `USD 다운로드 (${shellMode === "auto" ? "AI 자동 두께" : shellMode === "solid" ? "솔리드" : `쉘 ${shellMm}mm`})`}
                  </button>
                  <span className="muted" style={{ fontSize: 11 }}>위에서 고른 두께로 그 자리에서 만들어 받습니다(질량·관성 반영).</span>
                </div>
              </>
            )}
            {result.asset && <SpinViewer assetId={result.asset.id} label="🖱 인터랙티브 RTX 뷰어 (드래그·휠)" />}
          </div>
          {result.validation.violations.length > 0 && (
            <div className="card">
              <label>usdchecker 위반</label>
              <pre>{result.validation.violations.join("\n")}</pre>
            </div>
          )}
        </>
      )}
    </>
  );
}
