"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, downloadAsset, submitAndPoll } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";
import Tip from "../Tip";
import NumberInput from "../NumberInput";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type TreeNode = { path: string; name: string; type: string; children: TreeNode[] };
type MeshData = { path: string; name: string; vertices: number[]; faces: number[] };
type Joint = { type: string; parent: string; child: string; axis: string; pivot: number[] | null; lower?: number; upper?: number; reason?: string };

async function postForm<T>(path: string, fd: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) { let d = `오류 (${res.status})`; try { d = (await res.json()).detail ?? d; } catch { /* */ } throw new Error(d); }
  return (await res.json()) as T;
}

const WORLD = "(world)";
const AXIS_VEC: Record<string, [number, number, number]> = { X: [1, 0, 0], Y: [0, 1, 0], Z: [0, 0, 1] };
const under = (mp: string, np: string) => mp === np || mp.startsWith(np.replace(/\/$/, "") + "/");
const leaf = (p: string) => p.split("/").filter(Boolean).pop() || p;

// 파이프라인 인라인 임베드용 — 입력 파일을 주입받고, build 완료 시 onComplete 로 결과 에셋을 돌려준다.
type EmbeddedCtx = { inputFile: File; onComplete: (a: AssetRec) => void };

export default function Articulation({ manifest, embedded }: WorkflowModuleProps & { embedded?: EmbeddedCtx }) {
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
  const [notes, setNotes] = useState<string[]>([]);
  const [ingestNote, setIngestNote] = useState<string | null>(null);
  const [upAxis, setUpAxis] = useState("Z");   // 스테이지 up-axis(미리보기를 똑바로 세우기 위함)
  const [drive, setDrive] = useState(true);     // 모터(drive) 포함 여부
  const [weldLoose, setWeldLoose] = useState(true);  // 관절 없는 강체 제자리 고정(떨어짐 방지)
  const [aiBusy, setAiBusy] = useState(false);
  const [aiNote, setAiNote] = useState<string | null>(null);
  const [aiContext, setAiContext] = useState("");        // AI 추론 힌트(선택)
  const [aiImages, setAiImages] = useState<File[]>([]);  // AI 추론용 실물 참조 사진(선택)
  const [aiMs, setAiMs] = useState<number | null>(null); // 마지막 추론 소요시간(ms)
  const [previewAll, setPreviewAll] = useState(false);   // 목록의 모든 관절 동시 미리보기

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
    setError(null); setBusy(true); setTree([]); setMeshes([]); setJoints([]); setResultAsset(null); setIngestNote(null);
    setChild(""); setParent(""); setPivot(null); setPreview(false); setAiNote(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await postForm<{ tree: TreeNode[]; meshes: MeshData[]; note?: string | null; up_axis?: string }>(`/api/workflows/articulation/ingest`, fd);
      setTree(r.tree); setMeshes(r.meshes); setIngestNote(r.note ?? null); setUpAxis(r.up_axis === "Y" ? "Y" : "Z");
      // 상위 2레벨 자동 펼침
      const ex = new Set<string>();
      const walk = (ns: TreeNode[], d: number) => ns.forEach((n) => { if (d < 2 && n.children.length) ex.add(n.path); walk(n.children, d + 1); });
      walk(r.tree, 0); setExpanded(ex);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  // 임베드(파이프라인 직접 모드): 주입된 파일을 마운트 시 자동 인제스트
  useEffect(() => { if (embedded?.inputFile) onFile(embedded.inputFile); /* eslint-disable-next-line */ }, []);

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
      if (upAxis === "Z") camera.up.set(0, 0, 1);   // USD Z-up 자산을 Three(Y-up)에서 똑바로 세움
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
      // 카메라 near/far 를 자산 크기에 맞춰 자동 조정 → 단위(mm/m)가 어떻든 항상 프레이밍(까만화면 방지)
      camera.near = Math.max(size / 1000, 1e-4); camera.far = size * 100; camera.updateProjectionMatrix();
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
      function jointMatrix(jtype: string, piv: number[] | null, axis: string, lower: number, upper: number, a: number) {
        const axv = new THREE.Vector3(...(AXIS_VEC[axis] ?? AXIS_VEC.Z)).normalize();
        const M = new THREE.Matrix4();
        if (jtype === "prismatic") { const d = lower + (upper - lower) * a; M.makeTranslation(axv.x * d, axv.y * d, axv.z * d); }
        else if (jtype === "fixed") M.identity();
        else {
          const p = new THREE.Vector3(...(piv ?? [0, 0, 0]));
          const ang = (lower + (upper - lower) * a) * Math.PI / 180;
          M.multiplyMatrices(new THREE.Matrix4().makeTranslation(p.x, p.y, p.z), new THREE.Matrix4().makeRotationAxis(axv, ang)).multiply(new THREE.Matrix4().makeTranslation(-p.x, -p.y, -p.z));
        }
        return M;
      }
      function loop() {
        raf = requestAnimationFrame(loop); const st = tr.current;
        if (st.previewAll && st.allJoints && st.allJoints.length) {
          const a = Math.sin(clock.getElapsedTime() * 1.5) * 0.5 + 0.5;
          for (const [, mesh] of meshMap) { mesh.matrixAutoUpdate = false; mesh.matrix.copy(mesh.userData.base); }
          for (const j of st.allJoints) {
            const M = jointMatrix(j.type, j._piv ?? null, j.axis, j.lower ?? 0, j.upper ?? 0, a);
            for (const [p, mesh] of meshMap) if (under(p, j.child)) mesh.matrix.copy(M).multiply(mesh.userData.base);
          }
          for (const [, mesh] of meshMap) mesh.matrixWorldNeedsUpdate = true;
        } else if (st.preview && st.child) {
          const a = Math.sin(clock.getElapsedTime() * 1.5) * 0.5 + 0.5;
          const M = jointMatrix(st.jtype, st.pivot ?? null, st.axis, st.lower, st.upper, a);
          for (const [p, mesh] of meshMap) if (under(p, st.child)) { mesh.matrixAutoUpdate = false; mesh.matrix.copy(M).multiply(mesh.userData.base); mesh.matrixWorldNeedsUpdate = true; }
        }
        controls.update(); renderer.render(scene, camera);
      }
      loop();
      tr.current = { THREE, meshMap, pivotMesh, arrow, size, dispose: () => { cancelAnimationFrame(raf); renderer.domElement.removeEventListener("pointerdown", onClick); renderer.dispose(); el.innerHTML = ""; } };
    })();
    return () => { alive = false; if (tr.current?.dispose) tr.current.dispose(); tr.current = {}; };
  }, [meshes, upAxis]);

  // 선택/피벗/축/미리보기 반영
  useEffect(() => {
    const st = tr.current; if (!st.meshMap) return; const THREE = st.THREE;
    for (const [p, mesh] of st.meshMap as Map<string, any>) {
      const e = child && under(p, child) ? 0x1c6b3a : parent && under(p, parent) ? 0x1f4f7a : 0x000000;
      (mesh.material as any).emissive = new THREE.Color(e);
      if (!preview && !previewAll) { mesh.matrixAutoUpdate = true; mesh.matrix.copy(mesh.userData.base); mesh.matrixWorldNeedsUpdate = true; }
    }
    const piv = pivot ?? (child ? bodyCentroid(child) : null);
    const showGizmo = !previewAll;   // 전체 미리보기 중엔 단일 피벗/축 기즈모 숨김
    if (showGizmo && piv && st.pivotMesh) { st.pivotMesh.visible = true; st.pivotMesh.position.set(piv[0], piv[1], piv[2]); }
    else if (st.pivotMesh) st.pivotMesh.visible = false;
    if (showGizmo && st.arrow && piv && jtype !== "fixed") { st.arrow.visible = true; st.arrow.position.set(piv[0], piv[1], piv[2]); st.arrow.setDirection(new THREE.Vector3(...(AXIS_VEC[axis] ?? AXIS_VEC.Z)).normalize()); }
    else if (st.arrow) st.arrow.visible = false;
    const aj = joints.map((j) => ({ ...j, _piv: j.pivot ?? (j.child ? bodyCentroid(j.child) : null) }));
    Object.assign(st, { preview, previewAll, allJoints: aj, child, parent, axis, jtype, lower, upper, pivot: piv });
  }, [child, parent, axis, jtype, lower, upper, pivot, preview, previewAll, joints, meshes]);

  function pick(path: string) {
    const cur = selRef.current;
    if (cur.mode === "parent") setParent(path);
    else if (cur.mode === "pivot") { const c = bodyCentroid(path); if (c) setPivot(c); }
    else setChild(path);
  }
  function addJoint() {
    if (!child) { setError("자식(가동부) 노드를 고르세요."); return; }
    if (parent === child) { setError("부모와 자식이 같을 수 없습니다."); return; }
    if (jtype !== "fixed" && lower > upper) { setError("하한이 상한보다 클 수 없습니다 (하한 ≤ 상한)."); return; }
    if (jtype !== "fixed" && lower === upper) { setError("하한과 상한이 같으면 움직일 수 없습니다."); return; }
    setError(null);
    setJoints((js) => [...js, { type: jtype, parent, child, axis, pivot: pivot ?? bodyCentroid(child), lower: jtype === "fixed" ? undefined : lower, upper: jtype === "fixed" ? undefined : upper }]);
    setChild(""); setParent(""); setPivot(null); setPreview(false); setMode("child");
  }
  async function aiSuggest() {
    if (!file) { setError("먼저 STEP/USD 파일을 선택하세요."); return; }
    setError(null); setAiBusy(true); setAiNote(null); setAiMs(null);
    const t0 = Date.now();
    try {
      const fd = new FormData(); fd.append("file", file);
      if (aiContext.trim()) fd.append("context", aiContext.trim());
      for (const im of aiImages) fd.append("images", im);   // 실물 참조 사진(선택)
      // 잡+폴링: 부품 많은 어셈블리는 LLM 이 20~40초라 동기요청이면 프록시 30초 타임아웃에 걸림.
      const r = await submitAndPoll<{ joints: Joint[]; note?: string | null; parts_considered?: number }>(
        `/api/workflows/articulation/suggest-joints`, fd);
      const js = (r.joints ?? []).map((j) => ({ ...j, pivot: j.pivot ?? null }));
      setJoints(js);
      setAiNote(js.length
        ? `Claude가 부품 ${r.parts_considered ?? "?"}개를 보고 관절 ${js.length}개를 제안했습니다. 아래 목록에서 검토·수정·삭제 후 생성하세요.`
        : (r.note ?? "AI가 움직일 부품을 찾지 못했습니다. 수동으로 지정하세요."));
    } catch (e) { setError(String((e as Error).message)); }
    finally { setAiBusy(false); setAiMs(Date.now() - t0); }
  }
  function loadJoint(j: Joint) {
    setMode("child");
    setChild(j.child); setParent(j.parent || ""); setJtype(j.type); setAxis(j.axis || "Z");
    setLower(j.lower ?? -90); setUpper(j.upper ?? 90); setPivot(j.pivot ?? null);
    setPreviewAll(false); setPreview(true);
  }
  function updateJoint(i: number, patch: Partial<Joint>) {
    setJoints((js) => js.map((j, k) => (k === i ? { ...j, ...patch } : j)));
  }
  async function build() {
    if (!file) { setError("파일을 다시 선택하세요."); return; }
    setError(null); setBusy(true); setResultAsset(null); setNotes([]);
    try {
      const fd = new FormData(); fd.append("file", file); fd.append("joints", JSON.stringify(joints));
      fd.append("drive", String(drive));
      fd.append("weld_loose", String(weldLoose));
      const r = await postForm<{ asset: AssetRec; notes?: string[] }>(`/api/workflows/articulation/build`, fd);
      setResultAsset(r.asset);
      setNotes(r.notes ?? []);
      if (embedded) embedded.onComplete(r.asset);   // 파이프라인 재개
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
        {!embedded && <p className="muted">{manifest.description}</p>}
        {!embedded && <label>STEP/USD 파일 ({accept})</label>}
        {!embedded && <input type="file" accept={accept} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />}
        {embedded && <p className="muted">파이프라인 직접 모드 — 관절을 지정/편집하고 <b>관절 생성</b>을 누르면 다음 단계로 넘어갑니다.</p>}
        {busy && <p className="muted">처리 중… (대형 파일은 수십 초 걸릴 수 있어요)</p>}
        {ingestNote && (
          <p className="muted" style={{ marginTop: 8, fontSize: 12.5, padding: "8px 12px", borderRadius: 6, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>ⓘ {ingestNote}</p>
        )}
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {meshes.length > 0 && (
        <div className="card">
          <div style={{ marginBottom: 8, paddingBottom: 8, borderBottom: "1px solid var(--vsc-border)" }}>
            <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button onClick={aiSuggest} disabled={aiBusy || busy}>{aiBusy ? "🤖 추론 중…" : "🤖 AI 관절 추론 (Claude)"}</button>
              {aiMs != null && !aiBusy && <span className="muted" style={{ fontSize: 12 }}>Inference time: {(aiMs / 1000).toFixed(1)}s</span>}
              <span className="muted" style={{ fontSize: 12 }}>부품 형상·구조를 보고 가동부(문·뚜껑·서랍 등)와 관절을 자동 제안 → 검토·수정 후 생성</span>
            </div>
            <input type="text" value={aiContext} onChange={(e) => setAiContext(e.target.value)}
              placeholder="(선택) AI 힌트 — ex) 문이 2개 달린 캐비넷, 아래로 열리는 오븐, 앞으로 빠지는 서랍장, 위로 젖혀지는 박스 뚜껑"
              style={{ width: "100%", marginTop: 8, padding: "6px 8px", fontSize: 12.5 }} />
            <div className="row" style={{ gap: 8, alignItems: "center", marginTop: 8, flexWrap: "wrap" }}>
              <label className="ghost" style={{ margin: 0, padding: "4px 10px", fontSize: 12, cursor: "pointer", borderRadius: 6, border: "1px solid var(--vsc-border)" }}>
                📷 실물 사진 추가
                <input type="file" accept="image/*" multiple style={{ display: "none" }}
                  onChange={(e) => {
                    const picked = Array.from(e.target.files ?? []);   // value="" 로 초기화하기 전에 먼저 캡처(안 그러면 files 가 비워져 0개 추가됨)
                    e.target.value = "";
                    if (picked.length) setAiImages((prev) => [...prev, ...picked]);
                  }} />
              </label>
              <span className="muted" style={{ fontSize: 12 }}>(선택) 실물/제품 사진을 주면 AI가 어디가 움직이는지·여는 방향을 더 정확히 판단합니다.</span>
            </div>
            {aiImages.length > 0 && (
              <div className="row" style={{ gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                {aiImages.map((im, k) => (
                  <span key={k} className="muted" style={{ fontSize: 11.5, padding: "2px 8px", borderRadius: 12, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>
                    🖼 {im.name.length > 22 ? im.name.slice(0, 20) + "…" : im.name}
                    <button onClick={() => setAiImages((prev) => prev.filter((_, j) => j !== k))}
                      style={{ marginLeft: 6, background: "none", border: "none", color: "var(--vsc-fg-muted)", cursor: "pointer", padding: 0, fontSize: 12 }} title="제거">✕</button>
                  </span>
                ))}
              </div>
            )}
          </div>
          {aiNote && (
            <p className="muted" style={{ marginTop: 0, marginBottom: 8, fontSize: 12.5, padding: "8px 12px", borderRadius: 6, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>🤖 {aiNote}</p>
          )}
          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
            <label style={{ margin: 0 }}>편집 — 클릭 모드<Tip t="3D/트리에서 클릭했을 때 무엇을 지정할지. 자식=움직이는 부품, 부모=기준(고정)부품, 피벗=회전/이동 중심점." /></label>
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
            <div><label style={{ marginTop: 0 }}>종류<Tip t="관절 종류. revolute=축 둘레로 회전(경첩·바퀴), prismatic=축 방향 직선 이동(서랍·실린더), fixed=움직이지 않게 두 부품 결합." /></label>
              <select value={jtype} onChange={(e) => setJtype(e.target.value)}>
                <option value="revolute">revolute (회전)</option><option value="prismatic">prismatic (직선)</option><option value="fixed">fixed (고정)</option>
              </select></div>
            <div><label style={{ marginTop: 0 }}>축<Tip t="운동 축. revolute는 이 축 둘레로 회전, prismatic은 이 축 방향으로 이동. 화살표로 3D에 표시됩니다." /></label>
              <select value={axis} onChange={(e) => setAxis(e.target.value)} disabled={jtype === "fixed"}>
                <option value="X">X</option><option value="Y">Y</option><option value="Z">Z</option>
              </select></div>
            {jtype !== "fixed" && (<>
              <div><label style={{ marginTop: 0 }}>하한{jtype === "prismatic" ? "(m)" : "(°)"}<Tip t="가동 범위의 최소값. revolute는 각도(°), prismatic은 거리(m). 하한 ≤ 상한 이어야 합니다." /></label><NumberInput value={lower} onChange={(n) => setLower(n)} style={{ width: 76 }} /></div>
              <div><label style={{ marginTop: 0 }}>상한{jtype === "prismatic" ? "(m)" : "(°)"}<Tip t="가동 범위의 최대값. revolute는 각도(°), prismatic은 거리(m). 하한과 같으면 움직일 수 없습니다." /></label><NumberInput value={upper} onChange={(n) => setUpper(n)} style={{ width: 76, borderColor: lower >= upper ? "#f48771" : undefined }} /></div>
              <button className={preview ? "" : "ghost"} onClick={() => { setPreview((v) => !v); setPreviewAll(false); }} disabled={!child || lower >= upper}>{preview ? "■ 미리보기" : "▶ 미리보기"}</button>
            </>)}
            {parent && <button className="ghost" onClick={() => setParent("")}>부모=world</button>}
            {pivot && <button className="ghost" onClick={() => setPivot(null)}>피벗=중심</button>}
            <button onClick={addJoint} disabled={!child || (jtype !== "fixed" && lower >= upper)}>관절 추가</button>
          </div>
          {jtype !== "fixed" && lower >= upper && (
            <p className="err" style={{ marginTop: 6 }}>하한({lower})은 상한({upper})보다 작아야 합니다.</p>
          )}
        </div>
      )}

      {joints.length > 0 && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
            <label style={{ margin: 0 }}>관절 목록 ({joints.length})</label>
            <button className={previewAll ? "" : "ghost"} style={{ padding: "3px 12px" }}
              onClick={() => { setPreviewAll((v) => !v); setPreview(false); }} disabled={!meshes.length}>
              {previewAll ? "■ 전체 미리보기 정지" : "▶ 전체 미리보기"}
            </button>
          </div>
          <p className="muted" style={{ fontSize: 11.5, margin: "2px 0 6px" }}>각 항목의 <b>보기</b>로 하나씩 확인하거나, <b>전체 미리보기</b>로 모든 관절을 동시에 움직여 봅니다.</p>
          {joints.map((j, i) => {
            const unit = j.type === "prismatic" ? "m" : "°";
            const badRange = j.type !== "fixed" && j.lower != null && j.upper != null && j.lower >= j.upper;
            return (
            <div key={i} style={{ borderBottom: "1px solid #2d2d30", padding: "5px 0" }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span style={{ fontSize: 13 }}><b>{leaf(j.child)}</b> ↔ {j.parent ? leaf(j.parent) : "world"} · {j.type}</span>
                <span className="row" style={{ gap: 4 }}>
                  <button className="ghost" style={{ padding: "2px 8px" }} onClick={() => loadJoint(j)}>보기</button>
                  <button className="ghost" style={{ padding: "2px 8px" }} onClick={() => setJoints((js) => js.filter((_, k) => k !== i))}>삭제</button>
                </span>
              </div>
              {j.type !== "fixed" && (
                <div className="row" style={{ gap: 8, alignItems: "center", marginTop: 4, flexWrap: "wrap", fontSize: 12 }}>
                  <label style={{ margin: 0, fontWeight: 400 }}>축&nbsp;
                    <select value={j.axis} onChange={(e) => updateJoint(i, { axis: e.target.value })} style={{ padding: "2px 4px" }}>
                      <option value="X">X</option><option value="Y">Y</option><option value="Z">Z</option>
                    </select>
                  </label>
                  <label style={{ margin: 0, fontWeight: 400 }}>하한({unit})&nbsp;
                    <NumberInput value={j.lower ?? 0} onChange={(n) => updateJoint(i, { lower: n })} style={{ width: 70 }} />
                  </label>
                  <label style={{ margin: 0, fontWeight: 400 }}>상한({unit})&nbsp;
                    <NumberInput value={j.upper ?? 0} onChange={(n) => updateJoint(i, { upper: n })} style={{ width: 70, borderColor: badRange ? "#f48771" : undefined }} />
                  </label>
                  <span className="muted" style={{ fontSize: 11 }}>열리는 방향이 반대면 하한/상한 부호를 바꾸거나 축을 조정하세요.</span>
                </div>
              )}
              {badRange && <p className="err" style={{ margin: "2px 0 0", fontSize: 11.5 }}>하한이 상한보다 크거나 같습니다.</p>}
              {j.reason && <p className="muted" style={{ margin: "3px 0 0", fontSize: 11.5 }}>🤖 {j.reason}</p>}
            </div>
            );
          })}
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 10 }}>
            <input type="checkbox" checked={drive} onChange={(e) => setDrive(e.target.checked)} style={{ width: "auto" }} />
            모터(drive) 포함<Tip t="켜면 회전·직선 관절에 위치 드라이브(모터)를 답니다. 모터가 있으면 시뮬 Play 시 관절이 목표각(기본 닫힘)에 유지됩니다. 끄면 모터 없는 자유 관절이라 중력에 저절로 열립니다(경첩이 처지듯). NVIDIA SimReady Prop 납품엔 모터가 필수는 아닙니다(drive는 Robot-Body 프로파일 전용)." />
          </label>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, marginTop: 6 }}>
            <input type="checkbox" checked={weldLoose} onChange={(e) => setWeldLoose(e.target.checked)} style={{ width: "auto" }} />
            관절 없는 부품 제자리 고정 (떨어짐 방지)<Tip t="입력 USD에 이미 강체(RigidBody)가 들어있으면(예: 물성 카드 산출물), 관절이 없는 부품은 시뮬 시 중력에 떨어져 나갑니다. 켜면 그런 자유 강체를 월드에 고정해 제자리에 머무르게 합니다. 그 부품을 움직이게 하려면 대신 관절을 추가하세요." />
          </label>
          <div style={{ marginTop: 10 }}>
            <button onClick={build} disabled={busy || joints.some((j) => j.type !== "fixed" && j.lower != null && j.upper != null && j.lower >= j.upper)}>
              {busy ? "생성 중…" : "관절 USD 생성"}
            </button>
          </div>
        </div>
      )}

      {resultAsset && (
        <div className="card">
          <label style={{ margin: 0 }}>결과 USD (UsdPhysics 관절)</label>
          <p className="muted">{resultAsset.filename} · {(resultAsset.bytes / 1024).toFixed(1)} KB</p>
          <p className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            ✓ 축=up-axis·앵커 정합(요동 방지)·베이스 고정(kinematic)·ArticulationRoot·질량·충돌형상 자동판별(오목=convexDecomposition){drive ? "·Drive(모터)" : " (모터 없음)"}까지 자동 적용된 sim-ready 관절입니다.
          </p>
          {notes.length > 0 && (
            <div style={{ marginTop: 8, padding: "8px 12px", borderRadius: 6, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>
              {notes.map((n, i) => (
                <p key={i} className="err" style={{ margin: "2px 0", fontSize: 12.5 }}>⚠ {n}</p>
              ))}
            </div>
          )}
          <div className="row" style={{ marginTop: 8 }}><button className="ghost" onClick={() => downloadAsset(resultAsset.download_url, resultAsset.filename)}>USD 다운로드</button></div>
          <SpinViewer assetId={resultAsset.id} label="🖱 인터랙티브 RTX 뷰어 (드래그 회전)" />
        </div>
      )}
    </>
  );
}
