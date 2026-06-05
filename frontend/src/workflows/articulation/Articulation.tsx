"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, downloadFile } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Part = { name: string; centroid_m: number[]; size_mm: number[]; vertices: number[]; faces: number[] };
type Joint = { type: string; parent: string; child: string; axis: string; pivot: number[] | null; lower?: number; upper?: number };

async function postForm<T>(path: string, fd: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) { let d = `오류 (${res.status})`; try { d = (await res.json()).detail ?? d; } catch { /* */ } throw new Error(d); }
  return (await res.json()) as T;
}

const WORLD = "(world)";
const AXIS_VEC: Record<string, [number, number, number]> = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };

export default function Articulation({ manifest }: WorkflowModuleProps) {
  const accept = (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ?? ".step,.stp,.usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parts, setParts] = useState<Part[]>([]);
  const [mode, setMode] = useState<"child" | "parent" | "pivot">("child");
  const [child, setChild] = useState("");
  const [parent, setParent] = useState(WORLD);
  const [axis, setAxis] = useState("Z");
  const [jtype, setJtype] = useState("revolute");
  const [lower, setLower] = useState(-90);
  const [upper, setUpper] = useState(90);
  const [pivot, setPivot] = useState<number[] | null>(null);
  const [joints, setJoints] = useState<Joint[]>([]);
  const [preview, setPreview] = useState(false);
  const [resultAsset, setResultAsset] = useState<AssetRec | null>(null);

  const mount = useRef<HTMLDivElement | null>(null);
  const tr = useRef<any>({});            // three.js 상태
  const sel = useRef<{ child: string; parent: string; mode: string }>({ child: "", parent: WORLD, mode: "child" });
  useEffect(() => { sel.current = { child, parent, mode }; }, [child, parent, mode]);

  // ───── ingest ─────
  async function onFile(f: File | null) {
    setFile(f);
    if (!f) return;
    setError(null); setBusy(true); setParts([]); setJoints([]); setResultAsset(null);
    setChild(""); setParent(WORLD); setPivot(null); setPreview(false);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await postForm<{ parts: Part[] }>(`/api/workflows/articulation/ingest`, fd);
      setParts(r.parts);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  // ───── three.js 씬 구성 ─────
  useEffect(() => {
    if (!parts.length || !mount.current) return;
    let alive = true;
    (async () => {
      const THREE = await import("three");
      const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");
      if (!alive || !mount.current) return;
      const el = mount.current;
      const W = el.clientWidth || 800, H = 440;
      const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0d1117);
      const camera = new THREE.PerspectiveCamera(45, W / H, 0.001, 1000);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(W, H); renderer.setPixelRatio(window.devicePixelRatio);
      el.innerHTML = ""; el.appendChild(renderer.domElement);
      scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.1));
      const dl = new THREE.DirectionalLight(0xffffff, 1.0); dl.position.set(1, 2, 1.5); scene.add(dl);
      const controls = new OrbitControls(camera, renderer.domElement);

      const meshes = new Map<string, any>();
      const box = new THREE.Box3();
      for (const p of parts) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(p.vertices), 3));
        g.setIndex(new THREE.BufferAttribute(new Uint32Array(p.faces), 1));
        g.computeVertexNormals();
        const m = new THREE.MeshStandardMaterial({ color: 0x9aa4b2, metalness: 0.1, roughness: 0.8, flatShading: false });
        const mesh = new THREE.Mesh(g, m); mesh.name = p.name;
        mesh.userData.baseMatrix = mesh.matrix.clone();
        scene.add(mesh); meshes.set(p.name, mesh); box.expandByObject(mesh);
      }
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3()).length() || 1;
      camera.position.copy(center).add(new THREE.Vector3(size * 0.8, size * 0.6, size * 0.9));
      controls.target.copy(center); controls.update();

      const pivotMesh = new THREE.Mesh(new THREE.SphereGeometry(size * 0.02, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffcc00 }));
      pivotMesh.visible = false; scene.add(pivotMesh);
      const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), center, size * 0.4, 0x4ec9b0, size * 0.08, size * 0.05);
      arrow.visible = false; scene.add(arrow);

      const ray = new THREE.Raycaster(); const ndc = new THREE.Vector2();
      function onClick(ev: PointerEvent) {
        const rect = renderer.domElement.getBoundingClientRect();
        ndc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
        ndc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        ray.setFromCamera(ndc, camera);
        const hits = ray.intersectObjects([...meshes.values()], false);
        if (!hits.length) return;
        const hit = hits[0]; const nm = hit.object.name; const cur = sel.current;
        if (cur.mode === "pivot") {
          const pt = hit.point; setPivot([pt.x, pt.y, pt.z]);
        } else if (cur.mode === "parent") {
          setParent(nm);
        } else {
          setChild(nm);
        }
      }
      renderer.domElement.addEventListener("pointerdown", onClick);

      let raf = 0;
      const clock = new THREE.Clock();
      function loop() {
        raf = requestAnimationFrame(loop);
        const st = tr.current;
        // 미리보기 애니메이션
        if (st.preview && st.child && meshes.get(st.child)) {
          const mesh = meshes.get(st.child);
          const t = clock.getElapsedTime();
          const a = (Math.sin(t * 1.5) * 0.5 + 0.5);
          const piv = st.pivot ? new THREE.Vector3(...st.pivot) : new THREE.Vector3(...(parts.find((p) => p.name === st.child)?.centroid_m ?? [0, 0, 0]));
          const axv = new THREE.Vector3(...(AXIS_VEC[st.axis] ?? AXIS_VEC.Z));
          const M = new THREE.Matrix4();
          if (st.jtype === "prismatic") {
            const d = st.lower + (st.upper - st.lower) * a;
            M.makeTranslation(axv.x * d, axv.y * d, axv.z * d);
          } else if (st.jtype === "fixed") {
            M.identity();
          } else {
            const ang = (st.lower + (st.upper - st.lower) * a) * Math.PI / 180;
            const T1 = new THREE.Matrix4().makeTranslation(piv.x, piv.y, piv.z);
            const R = new THREE.Matrix4().makeRotationAxis(axv.normalize(), ang);
            const T0 = new THREE.Matrix4().makeTranslation(-piv.x, -piv.y, -piv.z);
            M.multiplyMatrices(T1, R).multiply(T0);
          }
          mesh.matrixAutoUpdate = false;
          mesh.matrix.copy(M).multiply(mesh.userData.baseMatrix);
          mesh.matrixWorldNeedsUpdate = true;
        }
        controls.update();
        renderer.render(scene, camera);
      }
      loop();

      tr.current = { THREE, scene, camera, renderer, controls, meshes, pivotMesh, arrow, box, size, center,
        dispose: () => { cancelAnimationFrame(raf); renderer.domElement.removeEventListener("pointerdown", onClick); renderer.dispose(); el.innerHTML = ""; } };
      // 초기 상태 동기화
      tr.current.preview = false;
    })();
    return () => { alive = false; if (tr.current?.dispose) tr.current.dispose(); tr.current = {}; };
  }, [parts]);

  // 선택/피벗/축/미리보기 → three 반영
  useEffect(() => {
    const st = tr.current; if (!st.meshes) return;
    const THREE = st.THREE;
    for (const [nm, mesh] of st.meshes as Map<string, any>) {
      const e = nm === child ? 0x1c6b3a : nm === parent ? 0x1f4f7a : 0x000000;
      (mesh.material as any).emissive = new THREE.Color(e);
      if (!st.preview) { mesh.matrixAutoUpdate = true; mesh.matrix.copy(mesh.userData.baseMatrix); mesh.matrixWorldNeedsUpdate = true; }
    }
    // 피벗 마커
    const piv = pivot ?? (child ? parts.find((p) => p.name === child)?.centroid_m ?? null : null);
    if (piv && st.pivotMesh) { st.pivotMesh.visible = true; st.pivotMesh.position.set(piv[0], piv[1], piv[2]); }
    else if (st.pivotMesh) st.pivotMesh.visible = false;
    // 축 화살표
    if (st.arrow && piv && jtype !== "fixed") {
      st.arrow.visible = true; st.arrow.position.set(piv[0], piv[1], piv[2]);
      st.arrow.setDirection(new THREE.Vector3(...(AXIS_VEC[axis] ?? AXIS_VEC.Z)).normalize());
    } else if (st.arrow) st.arrow.visible = false;
    // 미리보기 파라미터
    Object.assign(st, { preview, child, parent, axis, jtype, lower, upper, pivot });
  }, [child, parent, axis, jtype, lower, upper, pivot, preview, parts]);

  function addJoint() {
    if (!child) { setError("자식(가동부) 부품을 3D에서 클릭하세요."); return; }
    if (parent === child) { setError("부모와 자식이 같을 수 없습니다."); return; }
    setError(null);
    setJoints((js) => [...js, { type: jtype, parent: parent === WORLD ? "" : parent, child, axis,
      pivot: pivot ?? null, lower: jtype === "fixed" ? undefined : lower, upper: jtype === "fixed" ? undefined : upper }]);
    setChild(""); setParent(WORLD); setPivot(null); setPreview(false); setMode("child");
  }
  function removeJoint(i: number) { setJoints((js) => js.filter((_, k) => k !== i)); }

  async function build() {
    if (!file) { setError("파일을 다시 선택하세요."); return; }
    setError(null); setBusy(true); setResultAsset(null);
    try {
      const fd = new FormData(); fd.append("file", file); fd.append("joints", JSON.stringify(joints));
      const r = await postForm<{ asset: AssetRec }>(`/api/workflows/articulation/build`, fd);
      setResultAsset(r.asset);
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

      {parts.length > 0 && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
            <label style={{ margin: 0 }}>3D 편집 — 모드: 클릭 동작</label>
            <span className="row" style={{ gap: 6 }}>
              {(["child", "parent", "pivot"] as const).map((m) => (
                <button key={m} className={mode === m ? "" : "ghost"} style={{ padding: "3px 10px" }} onClick={() => setMode(m)}>
                  {m === "child" ? "자식 선택" : m === "parent" ? "부모 선택" : "피벗 지정"}
                </button>
              ))}
            </span>
          </div>
          <div ref={mount} style={{ width: "100%", height: 440, marginTop: 8, borderRadius: 8, overflow: "hidden", background: "#0d1117", cursor: "pointer" }} />
          <p className="muted" style={{ marginTop: 6 }}>
            모드를 고른 뒤 3D에서 부품/표면을 클릭하거나, 아래 <b>부품 목록</b>에서 골라도 됩니다 — <span style={{ color: "#4ec9b0" }}>자식=초록</span>, <span style={{ color: "#3794ff" }}>부모=파랑</span>, 노란 점=피벗, 화살표=축.
          </p>

          <div style={{ marginTop: 8 }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>부품 목록 ({parts.length}) — 행 클릭 = 현재 모드 적용 / 버튼으로 직접 지정</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(230px, 1fr))", gap: 6, maxHeight: 220, overflowY: "auto" }}>
              {parts.map((p) => (
                <div
                  key={p.name}
                  onClick={() => { if (mode === "parent") setParent(p.name); else if (mode === "pivot") setPivot(p.centroid_m); else setChild(p.name); }}
                  title={`${p.size_mm.join(" × ")} mm`}
                  style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6, cursor: "pointer",
                    border: "1px solid #3c3c3c", borderRadius: 6, padding: "5px 8px",
                    background: child === p.name ? "rgba(78,201,176,.15)" : parent === p.name ? "rgba(55,148,255,.15)" : "transparent",
                  }}
                >
                  <span style={{ fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
                  <span className="row" style={{ gap: 4, flex: "none" }} onClick={(e) => e.stopPropagation()}>
                    <button className="ghost" style={{ padding: "1px 7px", fontSize: 11 }} onClick={() => setChild(p.name)}>자식</button>
                    <button className="ghost" style={{ padding: "1px 7px", fontSize: 11 }} onClick={() => setParent(p.name)}>부모</button>
                  </span>
                </div>
              ))}
            </div>
          </div>

          <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginTop: 6 }}>
            <div><div className="muted" style={{ fontSize: 11 }}>자식</div><b>{child || "—"}</b></div>
            <div><div className="muted" style={{ fontSize: 11 }}>부모</div><b>{parent}</b></div>
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
            {jtype !== "fixed" && (
              <>
                <div><label style={{ marginTop: 0 }}>하한 {jtype === "prismatic" ? "(m)" : "(°)"}</label>
                  <input type="number" value={lower} onChange={(e) => setLower(Number(e.target.value))} style={{ width: 80 }} /></div>
                <div><label style={{ marginTop: 0 }}>상한 {jtype === "prismatic" ? "(m)" : "(°)"}</label>
                  <input type="number" value={upper} onChange={(e) => setUpper(Number(e.target.value))} style={{ width: 80 }} /></div>
                <button className={preview ? "" : "ghost"} onClick={() => setPreview((v) => !v)} disabled={!child}>{preview ? "■ 미리보기 중지" : "▶ 미리보기"}</button>
              </>
            )}
            {pivot && <button className="ghost" onClick={() => setPivot(null)}>피벗=중심</button>}
            <button onClick={addJoint}>관절 추가</button>
          </div>
        </div>
      )}

      {joints.length > 0 && (
        <div className="card">
          <label>관절 목록 ({joints.length})</label>
          {joints.map((j, i) => (
            <div className="row" key={i} style={{ justifyContent: "space-between", borderBottom: "1px solid #2d2d30", padding: "4px 0" }}>
              <span style={{ fontSize: 13 }}><b>{j.child}</b> ↔ {j.parent || "world"} · {j.type}{j.type !== "fixed" ? ` (${j.axis}, ${j.lower}~${j.upper})` : ""}{j.pivot ? " · 피벗지정" : ""}</span>
              <button className="ghost" style={{ padding: "2px 8px" }} onClick={() => removeJoint(i)}>삭제</button>
            </div>
          ))}
          <div style={{ marginTop: 10 }}><button onClick={build} disabled={busy}>{busy ? "생성 중…" : "관절 USD 생성"}</button></div>
        </div>
      )}

      {resultAsset && (
        <div className="card">
          <label style={{ margin: 0 }}>결과 USD (UsdPhysics 관절)</label>
          <p className="muted">{resultAsset.filename} · {(resultAsset.bytes / 1024).toFixed(1)} KB</p>
          <div className="row" style={{ marginTop: 8 }}><button className="ghost" onClick={() => downloadFile(resultAsset.download_url, resultAsset.filename)}>USD 다운로드</button></div>
          <SpinViewer assetId={resultAsset.id} label="🖱 인터랙티브 RTX 뷰어 (드래그 회전)" />
        </div>
      )}
    </>
  );
}
