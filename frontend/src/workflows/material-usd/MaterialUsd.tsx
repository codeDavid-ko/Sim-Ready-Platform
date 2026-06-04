"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, blobUrl, downloadFile, submitAndPoll } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";

type PartsJson = {
  input: string;
  in_units: string;
  up_axis: string;
  part_count: number;
  parts: { name: string; size_mm: number[]; vertex_count: number }[];
};
type AssetRec = { id: string; filename: string; bytes: number; download_url: string };

const WF = "material-usd";

async function postForm(path: string, fd: FormData): Promise<any> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authHeaders(), body: fd });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body?.detail ?? `오류 (${res.status})`);
  return body;
}

export default function MaterialUsd({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ??
    ".stl,.step,.stp,.usd,.usda,.usdc,.usdz,.glb,.gltf,.obj,.ply";

  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
  const [result, setResult] = useState<{ asset: AssetRec; preview?: AssetRec | null; info: any; usd_preview: string } | null>(null);
  const [resultGlb, setResultGlb] = useState<string | null>(null);
  const resultGlbRef = useRef<string | null>(null);
  // Omniverse(Isaac) 멀티앵글 렌더
  const [isaacVid, setIsaacVid] = useState<string | null>(null);
  const [isaacBusy, setIsaacBusy] = useState(false);
  const [isaacErr, setIsaacErr] = useState<string | null>(null);
  const isaacRefs = useRef<string[]>([]);

  const glbRef = useRef<string | null>(null);

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
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("in_units", inUnits);
      fd.append("up_axis", upAxis);
      const r = await postForm(`/api/workflows/${WF}/ingest`, fd);
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
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function runClassify(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!parts) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("parts", JSON.stringify(parts));
      fd.append("mode", mode);
      fd.append("text", text);
      for (const im of images) fd.append("images", im);
      const r = await postForm(`/api/workflows/${WF}/classify`, fd);
      setAssignmentText(JSON.stringify(r.assignment, null, 2));
      setLlmUsed(r.llm_used);
      setStep(2);
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
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
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("in_units", inUnits);
      fd.append("up_axis", upAxis);
      fd.append("assignment", JSON.stringify(asg));
      fd.append("add_light", String(addLight));
      const r = await postForm(`/api/workflows/${WF}/build`, fd);
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
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function runIsaac() {
    if (!result?.asset) return;
    setIsaacErr(null);
    setIsaacBusy(true);
    try {
      const fd = new FormData();
      fd.append("asset_id", result.asset.id);
      const r = await submitAndPoll<{ video: { download_url: string } | null }>(
        `/api/workflows/${WF}/render-submit`, fd,
      );
      if (r.video?.download_url) {
        const u = await blobUrl(r.video.download_url);
        isaacRefs.current.push(u);
        setIsaacVid(u);
      }
    } catch (err) {
      setIsaacErr(String((err as Error).message));
    } finally {
      setIsaacBusy(false);
    }
  }

  function reset() {
    setStep(0);
    setParts(null);
    setResult(null);
    setIsaacVid(null);
    setIsaacErr(null);
    isaacRefs.current.forEach((u) => URL.revokeObjectURL(u));
    isaacRefs.current = [];
    setAssignmentText("");
    setLlmUsed(null);
    if (glbRef.current) {
      URL.revokeObjectURL(glbRef.current);
      glbRef.current = null;
    }
    setGlbSrc(null);
  }

  const steps = ["1. 형상 업로드", "2. 재질 분류", "3. 검수", "4. USD 생성"];

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
                <label>입력 단위</label>
                <select value={inUnits} onChange={(e) => setInUnits(e.target.value)} style={selectStyle}>
                  <option value="m">m (SolidWorks STEP 기본)</option>
                  <option value="mm">mm</option>
                  <option value="cm">cm</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label>Up 축</label>
                <select value={upAxis} onChange={(e) => setUpAxis(e.target.value)} style={selectStyle}>
                  <option value="Y">Y-up (SolidWorks 기본)</option>
                  <option value="Z">Z-up</option>
                </select>
              </div>
            </div>
            <div style={{ marginTop: 12 }}>
              <button type="submit" disabled={busy}>{busy ? "형상 분석 중…" : "형상 분석 (ingest)"}</button>
            </div>
          </form>
        </div>
      )}

      {/* 뷰어 + 부품 목록 (step >= 1) */}
      {step >= 1 && parts && (
        <div className="card">
          <label>뷰어 — 부품 {parts.part_count}개 (mm · Z-up)</label>
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
            <label>분류 모드</label>
            <select value={mode} onChange={(e) => setMode(e.target.value as "1" | "2")} style={selectStyle}>
              <option value="1">모드 1 — 전체 이미지 + 부품 이름</option>
              <option value="2">모드 2 — 부품별 텍스트 설명</option>
            </select>
            {mode === "1" && (
              <>
                <label>참조 이미지 (여러 장 가능)</label>
                <input type="file" accept="image/*" multiple onChange={(e) => setImages(Array.from(e.target.files ?? []))} />
              </>
            )}
            <label>설명 텍스트 {mode === "2" ? "(부품별 설명)" : "(선택)"}</label>
            <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="예: 효성 크림색 제어 캐비닛, 도어는 살짝 밝게" />
            <p className="muted">Claude 자격증명(구독 토큰 `CLAUDE_CODE_OAUTH_TOKEN` 또는 API 키)이 있으면 LLM이 부품별 재질을 분류하고, 둘 다 없으면 기본 팔레트로 폴백합니다(어느 경우든 검수 단계에서 직접 수정 가능).</p>
            <div className="row" style={{ marginTop: 12 }}>
              <button type="submit" disabled={busy}>{busy ? "분류 중…" : "재질 분류 (classify)"}</button>
              <button type="button" className="ghost" onClick={() => setStep(0)}>← 이전</button>
            </div>
          </form>
        </div>
      )}

      {/* STEP 2 — review assignment */}
      {step === 2 && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label style={{ margin: 0 }}>assignment 검수 (수정 가능)</label>
            <span className={`badge ${llmUsed ? "ok" : "warn"}`}>{llmUsed ? "LLM 분류" : "기본 팔레트(자격증명 없음)"}</span>
          </div>
          <textarea
            rows={14}
            value={assignmentText}
            onChange={(e) => setAssignmentText(e.target.value)}
            style={{ fontFamily: "monospace", fontSize: 12.5 }}
          />
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 10 }}>
            <input type="checkbox" checked={addLight} onChange={(e) => setAddLight(e.target.checked)} style={{ width: "auto" }} />
            USD에 기본 라이트(DistantLight) 포함 — Isaac Sim에서 바로 보이게
          </label>
          <div className="row" style={{ marginTop: 12 }}>
            <button type="button" onClick={runBuild} disabled={busy}>{busy ? "USD 생성 중…" : "USD 생성 (build)"}</button>
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
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>Omniverse 렌더 (360° 회전 · 실제 vMaterials)</label>
              <button className="ghost" onClick={runIsaac} disabled={isaacBusy}>
                {isaacBusy ? "렌더 중… (Isaac Sim)" : "Omniverse로 렌더"}
              </button>
            </div>
            <p className="muted">Isaac Sim 6.0 RTX로 결과 USD를 360° 회전 렌더 — PBR 근사가 아닌 실제 MDL 룩. (약 1~2분)</p>
            {isaacErr && <p className="err">{isaacErr}</p>}
            {isaacVid && (
              <video src={isaacVid} controls autoPlay loop muted playsInline style={{ width: "100%", borderRadius: 8, background: "#0d1117" }} />
            )}
          </div>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <label style={{ margin: 0 }}>완료 — 재질 바인딩 USD</label>
              <span className="badge ok">저장소 등록됨</span>
            </div>
            <p className="muted">
              메시 {result.info.meshes} · 재질 {Array.isArray(result.info.materials) ? result.info.materials.join(", ") : ""} · 크기(mm) {Array.isArray(result.info.size_mm) ? result.info.size_mm.join(" × ") : ""}
            </p>
            <p className="muted">{result.asset.filename} · {(result.asset.bytes / 1024).toFixed(1)} KB</p>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="ghost" onClick={() => downloadFile(result.asset.download_url, result.asset.filename)}>USD 다운로드</button>
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
