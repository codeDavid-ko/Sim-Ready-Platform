"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, downloadFile } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type TreeNode = { path: string; name: string; type: string; children: TreeNode[] };
type MeshData = { path: string; name: string; vertices: number[]; faces: number[] };
type Joint = { type: string; parent: string; child: string; axis: string; pivot: number[] | null; lower?: number; upper?: number };

async function postForm<T>(path: string, fd: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) { let d = `오류 (${res.status})`; try { d = (await res.json()).detail ?? d; } catch { /* */ } throw new Error(d); }
  return (await res.json()) as T;
}

const WORLD = "(world)";
const AXIS_VEC: Record<string, [number, number, number]> = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };
const under = (mp: string, np: string) => mp === np || mp.startsWith(np.replace(/\/$/, "") + "/");
const leaf = (p: string) => p.split("/").filter(Boolean).pop() || p;

export default function Articulation({ manifest }: WorkflowModuleProps) {
  const accept = (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ?? ".step,.stp,.usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tree, setTree] = useState<TreeNode[]>([]);
  const [meshes, setMeshes] = useState<MeshData[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"child" | "parent" | "pivot">("child");
  const [child, setChild] = useState("");   // path
  const [parent, setParent] = useState(""); // path or "" = world
  const [axis, setAxis] = useState("Z");
  const [jtype, setJtype] = useState("revolute");
  const [lower, setLower] = useState(-90);
  const [upper, setUpper] = useState(90);
  const [pivot, setPivot] = useState<number[] | null>(null);
  const [joints, setJoints] = useState<Joint[]>([]);
  const [preview, setPreview] = useState(false);
  const [resultAsset, setResultAsset] = useState<AssetRec | null>(null);

  const mount = useRef<HTMLDivElement | null>(null);
  const tr = useRef<any>({});
  const selRef = useRef({ child: "", parent: "", mode: "child" });
  useEffect(() => { selRef.current = { child, parent, mode }; }, [child, parent, mode]);

  function bodyCentroid(path: string): number[] | null {
    const bm = meshes.filter((m) => under(m.path, path));
    if (!bm.length) return null;
    let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
    for (const m of bm) for (let i = 0; i < m.vertices.length; i += 3)
      for (let k = 0; k < 3; k++) { const v = m.vertices[i + k]; if (v < mn[k]) mn[k] = v; if (v > mx[k]) mx[k] = v; }
    return [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
  }

  async function onFile(f: File | null) {
    setFile(f);
    if (!f) return;
    setError(null); setBusy(true); setTree([]); setMeshes([]); setJoints([]); setResultAsset(null);
    setChild(""); setParent(""); setPivot(null); setPreview(false);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await postForm<{ tree: TreeNode[]; meshes: MeshData[] }>(`/api/workflows/articulation/ingest`, fd);
      setTree(r.tree); setMeshes(r.meshes);
      // 상위 2레벨 자동 펼침
      const ex = new Set<string>();
      const walk = (ns: TreeNode[], d: number) => ns.forEach((n) => { if (d < 2 && n.children.length) ex.add(n.path); walk(n.children, d + 1); });
      walk(r.tree, 0); setExpanded(ex);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  // three.js 씬
  useEffect(() => {
    if (!meshes.length || !mount.current) return;
    let alive = true;
    (async () => {
      const THREE = await import("three");
      const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");
      if (!alive || !mount.current) return;
      const el = mount.current; const W = el.clientWidth || 800, H = 420;
      const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0d1117);
      const camera = new THREE.PerspectiveCamera(45, W / H, 0.001, 5000);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(W, H); renderer.setPixelRatio(window.devicePixelRatio);
      el.innerHTML = ""; el.appendChild(renderer.domElement);
      scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.1));
      const dl = new THREE.DirectionalLight(0xffffff, 1.0); dl.position.set(1, 2, 1.5); scene.add(dl);
      const controls = new OrbitControls(camera, renderer.domElement);
      const meshMap = new Map<string, any>(); const box = new THREE.Box3();
      for (const m of meshes) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(m.vertices), 3));
        g.setIndex(new THREE.BufferAttribute(new Uint32Array(m.faces), 1)); g.computeVertexNormals();
        const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0x9aa4b2, metalness: 0.1, roughness: 0.85 }));
        mesh.name = m.path; mesh.userData.base = mesh.matrix.clone();
        scene.add(mesh); meshMap.set(m.path, mesh); box.expandByObject(mesh);
      }
      const center = box.getCenter(new THREE.Vector3()); const size = box.getSize(new THREE.Vector3()).length() || 1;
      camera.position.copy(center).add(new THREE.Vector3(size * 0.8, size * 0.6, size * 0.9));
      controls.target.copy(center); controls.update();
      const pivotMesh = new THREE.Mesh(new THREE.SphereGeometry(size * 0.018, 16, 16), new THREE.MeshBasicMaterial({ color: 0xffcc00 }));
      pivotMesh.visible = false; scene.add(pivotMesh);
      const arrow = new THREE.ArrowHelper(new THREE.Vector3(0, 0, 1), center, size * 0.4, 0x4ec9b0, size * 0.08, size * 0.05);
      arrow.visible = false; scene.add(arrow);
      const ray = new THREE.Raycaster(); const ndc = new THREE.Vector2();
      function onClick(ev: PointerEvent) {
        const rect = renderer.domElement.getBoundingClientRect();
        ndc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1; ndc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        ray.setFromCamera(ndc, camera);
        const hits = ray.intersectObjects([...meshMap.values()], false); if (!hits.length) return;
        const nm = hits[0].object.name; const cur = selRef.current;
        if (cur.mode === "pivot") { const p = hits[0].point; setPivot([p.x, p.y, p.z]); }
        else if (cur.mode === "parent") setParent(nm);
        else setChild(nm);
      }
      renderer.domElement.addEventListener("pointerdown", onClick);
      let raf = 0; const clock = new THREE.Clock();
      function loop() {
        raf = requestAnimationFrame(loop); const st = tr.current;
        if (st.preview && st.child) {
          const a = Math.sin(clock.getElapsedTime() * 1.5) * 0.5 + 0.5;
          const piv = new THREE.Vector3(...(st.pivot ?? [0, 0, 0]));
          const axv = new THREE.Vector3(...(AXIS_VEC[st.axis] ?? AXIS_VEC.Z)).normalize();
          const M = new THREE.Matrix4();
          if (st.jtype === "prismatic") { const d = st.lower + (st.upper - st.lower) * a; M.makeTranslation(axv.x * d, axv.y * d, axv.z * d); }
          else if (st.jtype === "fixed") M.identity();
          else { const ang = (st.lower + (st.upper - st.lower) * a) * Math.PI / 180;
            M.multiplyMatrices(new THREE.Matrix4().makeTranslation(piv.x, piv.y, piv.z), new THREE.Matrix4().makeRotationAxis(axv, ang)).multiply(new THREE.Matrix4().makeTranslation(-piv.x, -piv.y, -piv.z)); }
          for (const [p, mesh] of meshMap) if (under(p, st.child)) { mesh.matrixAutoUpdate = false; mesh.matrix.copy(M).multiply(mesh.userData.base); mesh.matrixWorldNeedsUpdate = true; }
        }
        controls.update(); renderer.render(scene, camera);
      }
      loop();
      tr.current = { THREE, meshMap, pivotMesh, arrow, size, dispose: () => { cancelAnimationFrame(raf); renderer.domElement.removeEventListener("pointerdown", onClick); renderer.dispose(); el.innerHTML = ""; } };
    })();
    return () => { alive = false; if (tr.current?.dispose) tr.current.dispose(); tr.current = {}; };
  }, [meshes]);

  // 선택/피벗/축/미리보기 반영
  useEffect(() => {
    const st = tr.current; if (!st.meshMap) return; const THREE = st.THREE;
    for (const [p, mesh] of st.meshMap as Map<string, any>) {
      const e = child && under(p, child) ? 0x1c6b3a : parent && under(p, parent) ? 0x1f4f7a : 0x000000;
      (mesh.material as any).emissive = new THREE.Color(e);
      if (!preview) { mesh.matrixAutoUpdate = true; mesh.matrix.copy(mesh.userData.base); mesh.matrixWorldNeedsUpdate = true; }
    }
    const piv = pivot ?? (child ? bodyCentroid(child) : null);
    if (piv && st.pivotMesh) { st.pivotMesh.visible = true; st.pivotMesh.position.set(piv[0], piv[1], piv[2]); }
    else if (st.pivotMesh) st.pivotMesh.visible = false;
    if (st.arrow && piv && jtype !== "fixed") { st.arrow.visible = true; st.arrow.position.set(piv[0], piv[1], piv[2]); st.arrow.setDirection(new THREE.Vector3(...(AXIS_VEC[axis] ?? AXIS_VEC.Z)).normalize()); }
    else if (st.arrow) st.arrow.visible = false;
    Object.assign(st, { preview, child, parent, axis, jtype, lower, upper, pivot: piv });
  }, [child, parent, axis, jtype, lower, upper, pivot, preview, meshes]);

  function pick(path: string) {
    const cur = selRef.current;
    if (cur.mode === "parent") setParent(path);
    else if (cur.mode === "pivot") { const c = bodyCentroid(path); if (c) setPivot(c); }
    else setChild(path);
  }
  function addJoint() {
    if (!child) { setError("자식(가동부) 노드를 고르세요."); return; }
    if (parent === child) { setError("부모와 자식이 같을 수 없습니다."); return; }
    setError(null);
    setJoints((js) => [...js, { type: jtype, parent, child, axis, pivot: pivot ?? bodyCentroid(child), lower: jtype === "fixed" ? undefined : lower, upper: jtype === "fixed" ? undefined : upper }]);
    setChild(""); setParent(""); setPivot(null); setPreview(false); setMode("child");
  }
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

  function toggle(p: string) { setExpanded((s) => { const n = new Set(s); n.has(p) ? n.delete(p) : n.add(p); return n; }); }
  function renderNode(n: TreeNode, depth: number) {
    const isChild = n.path === child, isParent = n.path === parent;
    const bg = isChild ? "rgba(78,201,176,.18)" : isParent ? "rgba(55,148,255,.18)" : "transparent";
    const hasKids = n.children.length > 0;
    return (
      <div key={n.path}>
        <div className="row" style={{ justifyContent: "space-between", paddingLeft: depth * 14, background: bg, borderRadius: 4 }}>
          <span className="row" style={{ gap: 4, minWidth: 0, cursor: "pointer" }} onClick={() => pick(n.path)}>
            <span style={{ width: 14, textAlign: "center", color: "#888", cursor: hasKids ? "pointer" : "default" }} onClick={(e) => { e.stopPropagation(); if (hasKids) toggle(n.path); }}>{hasKids ? (expanded.has(n.path) ? "▾" : "▸") : "·"}</span>
            <span style={{ fontSize: 12.5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: n.type === "Mesh" ? "#9d9d9d" : "#d4d4d4" }} title={n.path}>
              {n.name} <span style={{ fontSize: 10, color: "#6b7280" }}>{n.type}</span>
            </span>
          </span>
          <span className="row" style={{ gap: 3, flex: "none" }}>
            <button className="ghost" style={{ padding: "0 6px", fontSize: 11 }} onClick={() => setChild(n.path)}>자식</button>
            <button className="ghost" style={{ padding: "0 6px", fontSize: 11 }} onClick={() => setParent(n.path)}>부모</button>
          </span>
        </div>
        {hasKids && expanded.has(n.path) && n.children.map((c) => renderNode(c, depth + 1))}
      </div>
    );
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

      {meshes.length > 0 && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
            <label style={{ margin: 0 }}>편집 — 클릭 모드</label>
            <span className="row" style={{ gap: 6 }}>
              {(["child", "parent", "pivot"] as const).map((m) => (
                <button key={m} className={mode === m ? "" : "ghost"} style={{ padding: "3px 10px" }} onClick={() => setMode(m)}>
                  {m === "child" ? "자식" : m === "parent" ? "부모" : "피벗"}
                </button>
              ))}
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 10, marginTop: 8 }}>
            <div ref={mount} style={{ width: "100%", height: 420, borderRadius: 8, overflow: "hidden", background: "#0d1117", cursor: "pointer" }} />
            <div style={{ border: "1px solid #3c3c3c", borderRadius: 8, padding: 6, height: 420, overflowY: "auto" }}>
              <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>구조 트리 — 노드 클릭(현재 모드) / 버튼으로 지정</div>
              {tree.map((n) => renderNode(n, 0))}
            </div>
          </div>
          <p className="muted" style={{ marginTop: 6 }}>3D 또는 트리에서 선택 — <span style={{ color: "#4ec9b0" }}>자식=초록</span>, <span style={{ color: "#3794ff" }}>부모=파랑</span>. Xform 선택 시 그 아래 전체가 한 바디로 묶입니다. 노란 점=피벗, 화살표=축.</p>

          <div className="row" style={{ gap: 12, flexWrap: "wrap", alignItems: "flex-end", marginTop: 6 }}>
            <div><div className="muted" style={{ fontSize: 11 }}>자식</div><b>{child ? leaf(child) : "—"}</b></div>
            <div><div className="muted" style={{ fontSize: 11 }}>부모</div><b>{parent ? leaf(parent) : WORLD}</b></div>
            <div><label style={{ marginTop: 0 }}>종류</label>
              <select value={jtype} onChange={(e) => setJtype(e.target.value)}>
                <option value="revolute">revolute (회전)</option><option value="prismatic">prismatic (직선)</option><option value="fixed">fixed (고정)</option>
              </select></div>
            <div><label style={{ marginTop: 0 }}>축</label>
              <select value={axis} onChange={(e) => setAxis(e.target.value)} disabled={jtype === "fixed"}>
                <option value="X">X</option><option value="Y">Y</option><option value="Z">Z</option>
              </select></div>
            {jtype !== "fixed" && (<>
              <div><label style={{ marginTop: 0 }}>하한{jtype === "prismatic" ? "(m)" : "(°)"}</label><input type="number" value={lower} onChange={(e) => setLower(Number(e.target.value))} style={{ width: 76 }} /></div>
              <div><label style={{ marginTop: 0 }}>상한{jtype === "prismatic" ? "(m)" : "(°)"}</label><input type="number" value={upper} onChange={(e) => setUpper(Number(e.target.value))} style={{ width: 76 }} /></div>
              <button className={preview ? "" : "ghost"} onClick={() => setPreview((v) => !v)} disabled={!child}>{preview ? "■ 미리보기" : "▶ 미리보기"}</button>
            </>)}
            {parent && <button className="ghost" onClick={() => setParent("")}>부모=world</button>}
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
              <span style={{ fontSize: 13 }}><b>{leaf(j.child)}</b> ↔ {j.parent ? leaf(j.parent) : "world"} · {j.type}{j.type !== "fixed" ? ` (${j.axis}, ${j.lower}~${j.upper})` : ""}</span>
              <button className="ghost" style={{ padding: "2px 8px" }} onClick={() => setJoints((js) => js.filter((_, k) => k !== i))}>삭제</button>
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
