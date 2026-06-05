"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, blobUrl, downloadFile } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Part = { name: string; size_mm: number[]; centroid_m: number[] };
type Joint = { type: string; parent: string; child: string; axis: string };

async function postForm<T>(path: string, fd: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) {
    let d = `오류 (${res.status})`;
    try { d = (await res.json()).detail ?? d; } catch { /* */ }
    throw new Error(d);
  }
  return (await res.json()) as T;
}

const WORLD = "(world)";

export default function Articulation({ manifest }: WorkflowModuleProps) {
  const accept =
    (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ?? ".step,.stp,.usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [glb, setGlb] = useState<string | null>(null);
  const refs = useRef<string[]>([]);

  // 새 관절 편집 상태
  const [child, setChild] = useState("");
  const [parent, setParent] = useState(WORLD);
  const [jtype, setJtype] = useState("revolute");
  const [axis, setAxis] = useState("Z");
  const [joints, setJoints] = useState<Joint[]>([]);

  // 빌드 결과
  const [resultAsset, setResultAsset] = useState<AssetRec | null>(null);
  const [built, setBuilt] = useState<Joint[] | null>(null);

  useEffect(() => {
    import("@google/model-viewer").catch(() => {});
    return () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); };
  }, []);

  async function ingest(f: File) {
    setError(null); setBusy(true);
    setParts([]); setGlb(null); setJoints([]); setResultAsset(null); setBuilt(null); setChild(""); setParent(WORLD);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await postForm<{ parts: Part[]; glb: AssetRec }>(`/api/workflows/articulation/ingest`, fd);
      setParts(r.parts);
      if (r.glb?.download_url) { try { const u = await blobUrl(r.glb.download_url); refs.current.push(u); setGlb(u); } catch { /* */ } }
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  function onFile(f: File | null) {
    setFile(f);
    if (f) ingest(f);
  }

  function addJoint() {
    if (!child) { setError("자식(움직이는) 부품을 고르세요."); return; }
    if (parent === child) { setError("부모와 자식이 같을 수 없습니다."); return; }
    setError(null);
    setJoints((js) => [...js, { type: jtype, parent: parent === WORLD ? "" : parent, child, axis }]);
    setChild(""); setParent(WORLD);
  }
  function removeJoint(i: number) { setJoints((js) => js.filter((_, k) => k !== i)); }

  async function build() {
    if (!file) { setError("파일을 다시 선택하세요."); return; }
    setError(null); setBusy(true); setResultAsset(null); setBuilt(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("joints", JSON.stringify(joints));
      const r = await postForm<{ asset: AssetRec; joints: Joint[] }>(`/api/workflows/articulation/build`, fd);
      setResultAsset(r.asset);
      setBuilt(r.joints);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <div className="card">
        <p className="muted">{manifest.description}</p>
        <label>STEP/USD 파일 ({accept})</label>
        <input type="file" accept={accept} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
        {busy && <p className="muted">처리 중…</p>}
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {glb && (
        <div className="card">
          <label>3D 미리보기 (마우스 회전)</label>
          <model-viewer src={glb} camera-controls auto-rotate shadow-intensity="1"
            style={{ width: "100%", height: "340px", background: "#0d1117", borderRadius: "8px" }} />
        </div>
      )}

      {parts.length > 0 && (
        <div className="card">
          <label>부품 ({parts.length}) — 클릭해서 자식/부모로 지정</label>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 6 }}>
            {parts.map((p) => (
              <div key={p.name} className="row" style={{ justifyContent: "space-between", border: "1px solid #3c3c3c", borderRadius: 6, padding: "5px 8px", background: child === p.name ? "rgba(78,201,176,.12)" : parent === p.name ? "rgba(55,148,255,.12)" : "transparent" }}>
                <span style={{ fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={`${p.size_mm.join(" x ")} mm`}>{p.name}</span>
                <span className="row" style={{ gap: 4 }}>
                  <button className="ghost" style={{ padding: "2px 8px", fontSize: 11 }} onClick={() => setChild(p.name)}>자식</button>
                  <button className="ghost" style={{ padding: "2px 8px", fontSize: 11 }} onClick={() => setParent(p.name)}>부모</button>
                </span>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12, borderTop: "1px solid #3c3c3c", paddingTop: 12 }}>
            <label style={{ margin: 0 }}>새 관절</label>
            <div className="row" style={{ gap: 10, flexWrap: "wrap", alignItems: "flex-end", marginTop: 6 }}>
              <div><div className="muted" style={{ fontSize: 11 }}>자식(가동부)</div><b>{child || "—"}</b></div>
              <div><div className="muted" style={{ fontSize: 11 }}>부모(기준)</div><b>{parent}</b></div>
              <div><label style={{ marginTop: 0 }}>종류</label>
                <select value={jtype} onChange={(e) => setJtype(e.target.value)}>
                  <option value="revolute">revolute (회전)</option>
                  <option value="prismatic">prismatic (직선)</option>
                  <option value="fixed">fixed (고정)</option>
                </select></div>
              <div><label style={{ marginTop: 0 }}>축</label>
                <select value={axis} onChange={(e) => setAxis(e.target.value)} disabled={jtype === "fixed"}>
                  <option value="X">X</option><option value="Y">Y</option><option value="Z">Z</option>
                </select></div>
              <button onClick={addJoint}>관절 추가</button>
              <button className="ghost" onClick={() => setParent(WORLD)}>부모=world</button>
            </div>
            <p className="muted" style={{ marginTop: 6 }}>부품의 [자식]/[부모] 버튼으로 고르고 종류·축을 정한 뒤 추가. 피벗은 자식 중심 자동(회전축 위치).</p>
          </div>
        </div>
      )}

      {joints.length > 0 && (
        <div className="card">
          <label>관절 목록 ({joints.length})</label>
          {joints.map((j, i) => (
            <div className="row" key={i} style={{ justifyContent: "space-between", borderBottom: "1px solid #2d2d30", padding: "4px 0" }}>
              <span style={{ fontSize: 13 }}>
                <b>{j.child}</b> ↔ {j.parent || "world"} · {j.type}{j.type !== "fixed" ? ` (${j.axis})` : ""}
              </span>
              <button className="ghost" style={{ padding: "2px 8px" }} onClick={() => removeJoint(i)}>삭제</button>
            </div>
          ))}
          <div style={{ marginTop: 10 }}>
            <button onClick={build} disabled={busy}>{busy ? "생성 중…" : "관절 USD 생성"}</button>
          </div>
        </div>
      )}

      {resultAsset && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <label style={{ margin: 0 }}>결과 USD (UsdPhysics 관절)</label>
            <span className="badge ok">{built?.length ?? 0}개 관절</span>
          </div>
          <p className="muted">{resultAsset.filename} · {(resultAsset.bytes / 1024).toFixed(1)} KB</p>
          <div className="row" style={{ marginTop: 8, gap: 8 }}>
            <button className="ghost" onClick={() => downloadFile(resultAsset.download_url, resultAsset.filename)}>USD 다운로드</button>
          </div>
          <SpinViewer assetId={resultAsset.id} label="🖱 인터랙티브 RTX 뷰어 (드래그 회전)" />
        </div>
      )}
    </>
  );
}
