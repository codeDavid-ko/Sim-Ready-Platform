"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";
import JobProgress from "../JobProgress";
import Tip from "../Tip";

type PartsJson = {
  input: string;
  in_units: string;
  up_axis: string;
  part_count: number;
  parts: { name: string; size_mm: number[]; vertex_count: number }[];
  extract_ms?: number;  // 서버측 순수 형상 특징 추출(LLM 인풋) 시간 — GLB/디스플레이 제외
};
type AssetRec = { id: string; filename: string; bytes: number; download_url: string };

const WF = "material-usd";

export default function MaterialUsd({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".stl,.step,.stp,.usd,.usda,.usdc,.usdz,.glb,.gltf,.obj,.ply";

  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inferMs, setInferMs] = useState<number | null>(null);   // 재질 분류(추론) 소요시간(ms)

  // step 0
  const [file, setFile] = useState<File | null>(null);
  const [inUnits, setInUnits] = useState("m");
  const [upAxis, setUpAxis] = useState("Y");

  // step 0 result
  const [parts, setParts] = useState<PartsJson | null>(null);
  const [glbSrc, setGlbSrc] = useState<string | null>(null);

  // step 1
  const [mode, setMode] = useState<"1" | "2">("1");
  const [images, setImages] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [llmUsed, setLlmUsed] = useState<boolean | null>(null);

  // step 2
  const [assignmentText, setAssignmentText] = useState("");
  const [addLight, setAddLight] = useState(true);

  // step 3
  const [result, setResult] = useState<{ asset: AssetRec; usdz_asset?: AssetRec | null; preview?: AssetRec | null; info: any; usd_preview: string } | null>(null);
  const [resultGlb, setResultGlb] = useState<string | null>(null);
  const resultGlbRef = useRef<string | null>(null);

  const glbRef = useRef<string | null>(null);
  const acRef = useRef<AbortController | null>(null);  // 현재 단계 취소용

  // model-viewer 웹 컴포넌트 등록 (클라이언트 전용)
  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
  }, []);
  // object URL 정리
  useEffect(() => {
    return () => {
      if (glbRef.current) URL.revokeObjectURL(glbRef.current);
    };
  }, []);

  async function runIngest(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!file) {
      setError("3D 형상 파일을 선택하세요.");
      return;
    }
    setBusy(true);
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("in_units", inUnits);
      fd.append("up_axis", upAxis);
      const r = await submitAndPoll<{ parts: PartsJson; glb: AssetRec }>(
        `/api/workflows/${WF}/ingest-submit`, fd, { signal: ac.signal },
      );
      setParts(r.parts);
      try {
        const url = await blobUrl(r.glb.download_url);
        if (glbRef.current) URL.revokeObjectURL(glbRef.current);
        glbRef.current = url;
        setGlbSrc(url);
      } catch {
        setGlbSrc(null);
      }
      setStep(1);
    } catch (err) {
      setError(err instanceof CancelledError ? "취소되었습니다." : String((err as Error).message));
    } finally {
      setBusy(false);
      acRef.current = null;
    }
  }

  async function runClassify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!parts) return;
    setBusy(true);
    setInferMs(null);
    const t0 = Date.now();
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("parts", JSON.stringify(parts));
      fd.append("mode", mode);
      fd.append("text", text);
      for (const im of images) fd.append("images", im);
      const r = await submitAndPoll<{ assignment: any; llm_used: boolean }>(
        `/api/workflows/${WF}/classify-submit`, fd, { signal: ac.signal },
      );
      setAssignmentText(JSON.stringify(r.assignment, null, 2));
      setLlmUsed(r.llm_used);
      setStep(2);
    } catch (err) {
      setError(err instanceof CancelledError ? "취소되었습니다." : String((err as Error).message));
    } finally {
      setBusy(false);
      acRef.current = null;
      setInferMs(Date.now() - t0);
    }
  }

  async function runBuild() {
    setError(null);
    if (!file) return;
    let asg: unknown;
    try {
      asg = JSON.parse(assignmentText);
    } catch {
      setError("assignment 이 올바른 JSON 이 아닙니다.");
      return;
    }
    setBusy(true);
    const ac = new AbortController();
    acRef.current = ac;
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("in_units", inUnits);
      fd.append("up_axis", upAxis);
      fd.append("assignment", JSON.stringify(asg));
      fd.append("add_light", String(addLight));
      const r = await submitAndPoll<any>(`/api/workflows/${WF}/build-submit`, fd, { signal: ac.signal });
      setResult(r);
      if (r.preview?.download_url) {
        try {
          const url = await blobUrl(r.preview.download_url);
          if (resultGlbRef.current) URL.revokeObjectURL(resultGlbRef.current);
          resultGlbRef.current = url;
          setResultGlb(url);
        } catch { setResultGlb(null); }
      }
      setStep(3);
    } catch (err) {
      setError(err instanceof CancelledError ? "취소되었습니다." : String((err as Error).message));
    } finally {
      setBusy(false);
      acRef.current = null;
    }
  }

  function reset() {
    setStep(0);
    setParts(null);
    setResult(null);
    setAssignmentText("");
    setLlmUsed(null);
    if (glbRef.current) {
      URL.revokeObjectURL(glbRef.current);
      glbRef.current = null;
    }
    setGlbSrc(null);
  }

  const steps = ["1. 형상 업로드", "2. 재질 분류", "3. 검수", "4. USD 생성"];

  // assignment(JSON) + 부품 목록 → 부품별 재질 행. 부품 이름은 parts, 재질키는 index 로 조인.
  function materialRows(): { name: string; size: string; mat: string; mdl: string; reason: string }[] {
    if (!parts) return [];
    let asg: any;
    try { asg = JSON.parse(assignmentText); } catch { return []; }
    const pmap = asg?.parts ?? {};
    const pal = asg?.palette ?? {};
    const rmap = asg?.part_reason ?? {};
    const def = pmap.__default__;
    return parts.parts.map((p, i) => {
      const key = pmap[String(i)] ?? def;
      const spec = pal[key] ?? {};
      return { name: p.name, size: (p.size_mm || []).join(" × "), mat: spec.subId ?? key ?? "—", mdl: spec.mdl ?? "", reason: rmap[String(i)] ?? "" };
    });
  }

  function MaterialTable() {
    const rows = materialRows();
    if (!rows.length) return null;
    const distinct = new Set(rows.map((r) => r.mat)).size;
    return (
      <div style={{ overflowX: "auto", marginTop: 4 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid var(--vsc-border)" }}>
              <th style={{ padding: "6px 8px" }}>부품</th>
              <th style={{ padding: "6px 8px" }}>크기(mm)</th>
              <th style={{ padding: "6px 8px" }}>재질 (vMaterials)</th>
              <th style={{ padding: "6px 8px" }}>AI 선정 이유</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ borderBottom: "1px solid var(--vsc-border)" }} title={r.mdl}>
                <td style={{ padding: "6px 8px", fontWeight: 600 }}>{r.name}</td>
                <td style={{ padding: "6px 8px" }} className="muted">{r.size}</td>
                <td style={{ padding: "6px 8px" }}>{r.mat}{r.mdl ? <span className="muted" style={{ fontSize: 11 }}> · {r.mdl}</span> : null}</td>
                <td style={{ padding: "6px 8px", maxWidth: 280 }} className="muted">{r.reason || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: 11, marginTop: 4 }}>부품 {rows.length}개 · 서로 다른 재질 {distinct}종</p>
      </div>
    );
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
          {steps.map((s, i) => (
            <span key={i} className={`badge ${i === step ? "ok" : ""}`} style={{ background: i === step ? undefined : "#eef0f2", color: i === step ? undefined : "#6b7280" }}>
              {s}
            </span>
          ))}
        </div>
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {/* STEP 0 — 업로드 → ingest */}
      {step === 0 && (
        <div className="card">
          <form onSubmit={runIngest}>
            <label>3D 형상 파일 ({accept})</label>
            <input type="file" accept={accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <div className="row" style={{ gap: 16 }}>
              <div style={{ flex: 1 }}>
                <label>입력 단위<Tip t="업로드한 형상의 좌표 단위. 부품 크기(mm) 환산 기준이라 틀리면 스케일이 어긋납니다. SolidWorks STEP은 보통 m." /></label>
                <select value={inUnits} onChange={(e) => setInUnits(e.target.value)} style={selectStyle}>
                  <option value="m">m (SolidWorks STEP 기본)</option>
                  <option value="mm">mm</option>
                  <option value="cm">cm</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label>Up 축<Tip t="형상에서 어느 축이 위(중력 반대)인지. 잘못 고르면 모델이 눕거나 뒤집혀 보입니다. SolidWorks는 보통 Y-up." /></label>
                <select value={upAxis} onChange={(e) => setUpAxis(e.target.value)} style={selectStyle}>
                  <option value="Y">Y-up (SolidWorks 기본)</option>
                  <option value="Z">Z-up</option>
                </select>
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <button type="submit" disabled={busy}>{busy ? "형상 분석 중…" : "형상 분석 (ingest)"}</button>
              <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} />
            </div>
          </form>
        </div>
      )}

      {/* 뷰어 + 부품 목록 (step >= 1) */}
      {step >= 1 && parts && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
            <label style={{ margin: 0 }}>뷰어 — 부품 {parts.part_count}개 (mm · Z-up)</label>
            {parts.extract_ms != null && <span className="muted" style={{ fontSize: 12 }}>Feature extraction: {(parts.extract_ms / 1000).toFixed(1)}s</span>}
          </div>
          {glbSrc ? (
            <model-viewer
              src={glbSrc}
              camera-controls
              auto-rotate
              shadow-intensity="1"
              style={{ width: "100%", height: "320px", background: "#0d1117", borderRadius: "8px" }}
            />
          ) : (
            <p className="muted">미리보기 GLB를 불러오지 못했습니다(다운로드는 가능).</p>
          )}
          <div style={{ maxHeight: 160, overflow: "auto", marginTop: 8 }}>
            {parts.parts.map((p) => (
              <div className="check" key={p.name}>
                <span className="mark">▢</span>
                <span><b>{p.name}</b> — {p.size_mm.join(" × ")} mm · {p.vertex_count} verts</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* STEP 1 — classify */}
      {step === 1 && (
        <div className="card">
          <form onSubmit={runClassify}>
            <label>분류 모드<Tip t="재질 추론에 무엇을 단서로 줄지. 모드1=전체 사진+부품 이름으로 LLM이 자동 분류. 모드2=부품별 텍스트 설명을 직접 제공." /></label>
            <select value={mode} onChange={(e) => setMode(e.target.value as "1" | "2")} style={selectStyle}>
              <option value="1">모드 1 — 전체 이미지 + 부품 이름</option>
              <option value="2">모드 2 — 부품별 텍스트 설명</option>
            </select>
            {mode === "1" && (
              <>
                <label>참조 이미지 (여러 장 가능)<Tip t="실물/렌더 사진. LLM이 색·질감을 보고 부품별 재질을 분류합니다. 형상엔 영향 없고 재질 추론 입력으로만 쓰입니다." /></label>
                <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
              </>
            )}
            <label>설명 텍스트 {mode === "2" ? "(부품별 설명)" : "(선택)"}<Tip t="재질 분류를 돕는 자연어 힌트. 모드2에선 부품별 재질을 직접 적고, 모드1에선 전체 분위기·색감 등 보조 설명." /></label>
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="예: 효성 크림색 제어 캐비닛, 도어는 살짝 밝게" />
            <p className="muted">Claude 자격증명(구독 토큰 `CLAUDE_CODE_OAUTH_TOKEN` 또는 API 키)이 있으면 LLM이 부품별 재질을 분류하고, 둘 다 없으면 기본 팔레트로 폴백합니다(어느 경우든 검수 단계에서 직접 수정 가능).</p>
            <div className="row" style={{ marginTop: 12 }}>
              <button type="submit" disabled={busy}>{busy ? "분류 중…" : "재질 분류 (classify)"}</button>
              {inferMs != null && !busy && <span className="muted" style={{ fontSize: 12, alignSelf: "center" }}>Inference time: {(inferMs / 1000).toFixed(1)}s</span>}
              <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} />
              <button type="button" className="ghost" onClick={() => setStep(0)}>← 이전</button>
            </div>
          </form>
        </div>
      )}

      {/* STEP 2 — review assignment */}
      {step === 2 && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label style={{ margin: 0 }}>부품별 추론 재질<Tip t="LLM이 부품마다 배정한 vMaterials 재질입니다. 비슷한 부품은 같은 재질로 묶입니다. 아래 JSON에서 직접 고치면 이 표·생성 USD에 반영됩니다." /></label>
            <div className="row" style={{ gap: 8, alignItems: "center" }}>
              {inferMs != null && <span className="muted" style={{ fontSize: 12 }}>Inference time: {(inferMs / 1000).toFixed(1)}s</span>}
              <span className={`badge ${llmUsed ? "ok" : "warn"}`}>{llmUsed ? "LLM 분류" : "기본 팔레트(자격증명 없음)"}</span>
            </div>
          </div>
          <MaterialTable />
          <details style={{ marginTop: 10 }}>
            <summary className="muted" style={{ cursor: "pointer", fontSize: 12.5 }}>고급: assignment JSON 직접 수정</summary>
            <textarea
              rows={14}
              value={assignmentText}
              onChange={(e) => setAssignmentText(e.target.value)}
              style={{ fontFamily: "monospace", fontSize: 12.5, marginTop: 6 }}
            />
          </details>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 10 }}>
            <input type="checkbox" checked={addLight} onChange={(e) => setAddLight(e.target.checked)} style={{ width: "auto" }} />
            USD에 기본 라이트(DistantLight) 포함 — Isaac Sim에서 바로 보이게<Tip t="결과 USD에 기본 조명을 넣습니다. 켜면 Isaac/Omniverse에서 바로 밝게 보입니다. 이미 조명이 있는 씬에 합칠 거면 꺼서 중복을 피하세요." />
          </label>
          <div className="row" style={{ marginTop: 12 }}>
            <button type="button" onClick={runBuild} disabled={busy}>{busy ? "USD 생성 중…" : "USD 생성 (build)"}</button>
            <JobProgress busy={busy} onCancel={() => acRef.current?.abort()} />
            <button type="button" className="ghost" onClick={() => setStep(1)}>← 분류 다시</button>
          </div>
        </div>
      )}

      {/* STEP 3 — result */}
      {step === 3 && result && (
        <>
          {resultGlb && (
            <div className="card">
              <label>결과 미리보기 (PBR 근사 · 색/금속성/거칠기)</label>
              <model-viewer
                src={resultGlb}
                camera-controls
                auto-rotate
                shadow-intensity="1"
                style={{ width: "100%", height: "320px", background: "#0d1117", borderRadius: "8px" }}
              />
              <p className="muted">vMaterials MDL의 정밀 질감은 Omniverse/Isaac에서, 여기선 PBR 근사입니다.</p>
            </div>
          )}
          <div className="card">
            <label style={{ margin: 0 }}>인터랙티브 RTX 뷰어 (실제 vMaterials · 좌우 회전·상하 고도·휠 확대)</label>
            <p className="muted">Isaac Sim 6.0 RTX로 결과 USD를 렌더(약 수십 초~1~2분) 후 마우스로 돌려봅니다 — PBR 근사가 아닌 실제 MDL 룩.</p>
            {result.asset && <SpinViewer assetId={result.asset.id} label="🖱 RTX 뷰어 열기 (드래그·휠)" />}
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>완료 — 재질 바인딩 USD</label>
              <span className="badge ok">저장소 등록됨</span>
            </div>
            <p className="muted">
              메시 {result.info.meshes} · 재질 {Array.isArray(result.info.materials) ? result.info.materials.join(", ") : ""} · 크기(mm) {Array.isArray(result.info.size_mm) ? result.info.size_mm.join(" × ") : ""}
            </p>
            <MaterialTable />
            <p className="muted" style={{ marginTop: 6 }}>{result.asset.filename} · {(result.asset.bytes / 1024).toFixed(1)} KB</p>
            <div className="row" style={{ marginTop: 8, gap: 8, flexWrap: "wrap" }}>
              <button onClick={() => { const a = result.usdz_asset ?? result.asset; downloadAsset(a.download_url, a.filename); }}>재질 USD 다운로드</button>
              <button className="ghost" onClick={reset}>새로 시작</button>
            </div>
          </div>
          <div className="card">
            <label>USD 미리보기 (앞부분)</label>
            <pre>{result.usd_preview}</pre>
          </div>
        </>
      )}
    </>
  );
}

const selectStyle: React.CSSProperties = {
  width: "100%",
  padding: "8px 10px",
  border: "1px solid #cfd4da",
  borderRadius: 8,
  fontSize: 14,
};
