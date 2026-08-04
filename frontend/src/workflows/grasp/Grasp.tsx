"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, downloadAsset, submitAndPoll } from "@/lib/api";
import { authHeaders } from "@/lib/auth";
import type { WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";
import Tip from "../Tip";
import NumberInput from "../NumberInput";

type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type MeshData = { path: string; name: string; vertices: number[]; faces: number[] };
type Pt = [number, number, number];

async function postForm<T>(path: string, fd: FormData): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { method: "POST", headers: authHeaders(), body: fd });
  if (!res.ok) { let d = `오류 (${res.status})`; try { d = (await res.json()).detail ?? d; } catch { /* */ } throw new Error(d); }
  return (await res.json()) as T;
}

// 파이프라인 인라인 임베드용 — 입력 파일 주입 + build 완료 시 onComplete 로 결과 반환.
type EmbeddedCtx = { inputFile: File; onComplete: (a: AssetRec) => void };

export default function Grasp({ manifest, embedded }: WorkflowModuleProps & { embedded?: EmbeddedCtx }) {
  const accept = (manifest.io?.input as { file?: { accept?: string } } | undefined)?.file?.accept ?? ".usd,.usda,.usdc,.usdz";
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [meshes, setMeshes] = useState<MeshData[]>([]);
  const [upAxis, setUpAxis] = useState("Z");
  const [ingestNote, setIngestNote] = useState<string | null>(null);
  const [points, setPoints] = useState<[Pt, Pt] | null>(null);
  const [active, setActive] = useState<0 | 1>(0);   // 클릭 시 어느 점을 옮길지
  const [aiBusy, setAiBusy] = useState(false);
  const [aiNote, setAiNote] = useState<string | null>(null);
  const [aiContext, setAiContext] = useState("");
  const [resultAsset, setResultAsset] = useState<AssetRec | null>(null);

  const mount = useRef<HTMLDivElement | null>(null);
  const tr = useRef<any>({});
  const ptsRef = useRef<[Pt, Pt] | null>(null);
  const activeRef = useRef<0 | 1>(0);
  useEffect(() => { ptsRef.current = points; }, [points]);
  useEffect(() => { activeRef.current = active; }, [active]);

  async function onFile(f: File | null) {
    setFile(f);
    if (!f) return;
    setError(null); setBusy(true); setMeshes([]); setPoints(null); setResultAsset(null); setIngestNote(null); setAiNote(null);
    try {
      const fd = new FormData(); fd.append("file", f);
      const r = await postForm<{ meshes: MeshData[]; note?: string | null; up_axis?: string }>(`/api/workflows/grasp/ingest`, fd);
      setMeshes(r.meshes); setIngestNote(r.note ?? null); setUpAxis(r.up_axis === "Y" ? "Y" : "Z");
      // 초기 두 점 = bbox 중앙 부근(클릭/AI 전 보이게)
      let mn = [Infinity, Infinity, Infinity], mx = [-Infinity, -Infinity, -Infinity];
      for (const m of r.meshes) for (let i = 0; i < m.vertices.length; i += 3)
        for (let k = 0; k < 3; k++) { const v = m.vertices[i + k]; if (v < mn[k]) mn[k] = v; if (v > mx[k]) mx[k] = v; }
      const c: Pt = [(mn[0] + mx[0]) / 2, (mn[1] + mx[1]) / 2, (mn[2] + mx[2]) / 2];
      const ax = [mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]];
      const wi = ax.indexOf(Math.min(...ax));
      const p0: Pt = [...c] as Pt, p1: Pt = [...c] as Pt; p0[wi] = mn[wi]; p1[wi] = mx[wi];
      setPoints([p0, p1]); setActive(0);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  // 임베드(파이프라인 직접 모드): 주입된 파일 자동 인제스트
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
      if (upAxis === "Z") camera.up.set(0, 0, 1);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setSize(W, H); renderer.setPixelRatio(window.devicePixelRatio);
      el.innerHTML = ""; el.appendChild(renderer.domElement);
      scene.add(new THREE.HemisphereLight(0xffffff, 0x333344, 1.1));
      const dl = new THREE.DirectionalLight(0xffffff, 1.0); dl.position.set(1, 2, 1.5); scene.add(dl);
      const controls = new OrbitControls(camera, renderer.domElement);
      const meshList: any[] = []; const box = new THREE.Box3();
      for (const m of meshes) {
        const g = new THREE.BufferGeometry();
        g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(m.vertices), 3));
        g.setIndex(new THREE.BufferAttribute(new Uint32Array(m.faces), 1)); g.computeVertexNormals();
        const mesh = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: 0x9aa4b2, metalness: 0.1, roughness: 0.85 }));
        scene.add(mesh); meshList.push(mesh); box.expandByObject(mesh);
      }
      const center = box.getCenter(new THREE.Vector3()); const size = box.getSize(new THREE.Vector3()).length() || 1;
      camera.position.copy(center).add(new THREE.Vector3(size * 0.8, size * 0.6, size * 0.9));
      controls.target.copy(center); controls.update();
      const r = size * 0.022;
      const s0 = new THREE.Mesh(new THREE.SphereGeometry(r, 20, 20), new THREE.MeshBasicMaterial({ color: 0x4ec9b0 }));
      const s1 = new THREE.Mesh(new THREE.SphereGeometry(r, 20, 20), new THREE.MeshBasicMaterial({ color: 0x3794ff }));
      scene.add(s0); scene.add(s1);
      const lineGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      const line = new THREE.Line(lineGeo, new THREE.LineBasicMaterial({ color: 0xffcc00 }));
      scene.add(line);
      const ray = new THREE.Raycaster(); const ndc = new THREE.Vector2();
      function onClick(ev: PointerEvent) {
        const rect = renderer.domElement.getBoundingClientRect();
        ndc.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1; ndc.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
        ray.setFromCamera(ndc, camera);
        const hits = ray.intersectObjects(meshList, false); if (!hits.length) return;
        const p = hits[0].point; const a = activeRef.current; const cur = ptsRef.current;
        if (!cur) return;
        const np: [Pt, Pt] = [[...cur[0]] as Pt, [...cur[1]] as Pt];
        np[a] = [p.x, p.y, p.z];
        setPoints(np);
        if (a === 0) setActive(1);   // 첫 점 찍으면 자동으로 둘째 점 모드로
      }
      renderer.domElement.addEventListener("pointerdown", onClick);
      let raf = 0;
      function loop() { raf = requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); }
      loop();
      tr.current = { THREE, s0, s1, line, size, dispose: () => { cancelAnimationFrame(raf); renderer.domElement.removeEventListener("pointerdown", onClick); renderer.dispose(); el.innerHTML = ""; } };
      // 최초 점 반영
      _sync();
    })();
    return () => { alive = false; if (tr.current?.dispose) tr.current.dispose(); tr.current = {}; };
  }, [meshes, upAxis]);

  function _sync() {
    const st = tr.current; if (!st.s0 || !ptsRef.current) return;
    const [a, b] = ptsRef.current;
    st.s0.position.set(a[0], a[1], a[2]); st.s1.position.set(b[0], b[1], b[2]);
    const pos = st.line.geometry.attributes.position;
    pos.setXYZ(0, a[0], a[1], a[2]); pos.setXYZ(1, b[0], b[1], b[2]); pos.needsUpdate = true;
    // 활성 점 강조(스케일)
    st.s0.scale.setScalar(activeRef.current === 0 ? 1.5 : 1.0);
    st.s1.scale.setScalar(activeRef.current === 1 ? 1.5 : 1.0);
  }
  useEffect(() => { _sync(); }, [points, active]);

  async function aiSuggest() {
    if (!file) { setError("먼저 USD 파일을 선택하세요."); return; }
    setError(null); setAiBusy(true); setAiNote(null);
    try {
      const fd = new FormData(); fd.append("file", file);
      if (aiContext.trim()) fd.append("context", aiContext.trim());
      // 잡+폴링: 부품 많은 어셈블리는 LLM 이 30초 넘어 동기요청이면 프록시 타임아웃(500).
      const r = await submitAndPoll<{ points: Pt[]; part?: string; reason?: string | null; parts_considered?: number }>(
        `/api/workflows/grasp/suggest-grasp`, fd);
      if (r.points && r.points.length >= 2) { setPoints([r.points[0], r.points[1]]); setActive(0); }
      setAiNote(`🤖 Claude가 부품 ${r.parts_considered ?? "?"}개를 보고 파지 위치를 제안했습니다${r.part ? ` (대상: ${r.part.split("/").pop()})` : ""}. ${r.reason ?? ""} — 화면에서 점을 클릭해 옮길 수 있습니다.`);
    } catch (e) { setError(String((e as Error).message)); }
    finally { setAiBusy(false); }
  }

  function setCoord(pi: 0 | 1, ci: 0 | 1 | 2, v: number) {
    setPoints((prev) => { if (!prev) return prev; const np: [Pt, Pt] = [[...prev[0]] as Pt, [...prev[1]] as Pt]; np[pi][ci] = v; return np; });
  }

  async function build() {
    if (!file || !points) { setError("파일과 파지점을 확인하세요."); return; }
    setError(null); setBusy(true); setResultAsset(null);
    try {
      const fd = new FormData(); fd.append("file", file); fd.append("points", JSON.stringify(points));
      const r = await postForm<{ asset: AssetRec; info?: any }>(`/api/workflows/grasp/build`, fd);
      setResultAsset(r.asset);
      if (embedded) embedded.onComplete(r.asset);   // 파이프라인 재개
    } catch (e) { setError(String((e as Error).message)); }
    finally { setBusy(false); }
  }

  const fmt = (n: number) => (Math.abs(n) < 1e-4 ? "0" : n.toFixed(4));

  return (
    <>
      <div className="card">
        {!embedded && <p className="muted">{manifest.description}</p>}
        {!embedded && <label>USD 파일 ({accept})<Tip t="그래스프를 넣을 sim-ready USD. 보통 형상·재질·물성이 끝난 최종 USD에 파지점만 추가합니다." /></label>}
        {!embedded && <input type="file" accept={accept} onChange={(e) => onFile(e.target.files?.[0] ?? null)} />}
        {embedded && <p className="muted">파이프라인 직접 모드 — 파지점을 잡고 <b>그래스프 생성</b>을 누르면 다음 단계로 넘어갑니다.</p>}
        {busy && <p className="muted">처리 중…</p>}
        {ingestNote && <p className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>ⓘ {ingestNote}</p>}
      </div>

      {error && <div className="card"><p className="err">{error}</p></div>}

      {meshes.length > 0 && points && (
        <div className="card">
          <div style={{ marginBottom: 8, paddingBottom: 8, borderBottom: "1px solid var(--vsc-border)" }}>
            <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button onClick={aiSuggest} disabled={aiBusy || busy}>{aiBusy ? "🤖 추론 중…" : "🤖 AI 파지점 추론 (Claude)"}</button>
              <span className="muted" style={{ fontSize: 12 }}>형상을 보고 잡을 위치(두 점)를 제안 → 화면에서 클릭해 수정</span>
            </div>
            <input type="text" value={aiContext} onChange={(e) => setAiContext(e.target.value)}
              placeholder="(선택) 힌트 — 예: 앞문 손잡이를 잡는다 / 윗면 가장자리를 집는다"
              style={{ width: "100%", marginTop: 8, padding: "6px 8px", fontSize: 12.5 }} />
          </div>
          {aiNote && <p className="muted" style={{ marginTop: 0, marginBottom: 8, fontSize: 12.5, padding: "8px 12px", borderRadius: 6, background: "var(--vsc-elev)", border: "1px solid var(--vsc-border)" }}>{aiNote}</p>}

          <div className="row" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 6 }}>
            <label style={{ margin: 0 }}>클릭으로 옮길 점<Tip t="3D에서 형상을 클릭하면 선택된 점이 그 표면 위치로 이동합니다. 첫 점(P1)을 찍으면 자동으로 둘째 점(P2)으로 넘어갑니다." /></label>
            <span className="row" style={{ gap: 6 }}>
              <button className={active === 0 ? "" : "ghost"} style={{ padding: "3px 12px", color: active === 0 ? undefined : "#4ec9b0" }} onClick={() => setActive(0)}>● P1</button>
              <button className={active === 1 ? "" : "ghost"} style={{ padding: "3px 12px", color: active === 1 ? undefined : "#3794ff" }} onClick={() => setActive(1)}>● P2</button>
            </span>
          </div>

          <div ref={mount} style={{ width: "100%", height: 420, borderRadius: 8, overflow: "hidden", background: "#0d1117", cursor: "crosshair", marginTop: 8 }} />
          <p className="muted" style={{ marginTop: 6 }}><span style={{ color: "#4ec9b0" }}>P1=초록</span>, <span style={{ color: "#3794ff" }}>P2=파랑</span>, 노란선=그리퍼가 닫히는 파지선. 형상을 클릭해 점을 옮기세요(드래그=회전).</p>

          <div className="row" style={{ gap: 14, flexWrap: "wrap", marginTop: 8 }}>
            {([0, 1] as const).map((pi) => (
              <div key={pi} style={{ border: "1px solid #3c3c3c", borderRadius: 8, padding: "6px 10px" }}>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4, color: pi === 0 ? "#4ec9b0" : "#3794ff" }}>P{pi + 1} (m)</div>
                <div className="row" style={{ gap: 6 }}>
                  {([0, 1, 2] as const).map((ci) => (
                    <span key={ci} className="row" style={{ gap: 3, alignItems: "center" }}>
                      <span className="muted" style={{ fontSize: 11 }}>{"XYZ"[ci]}</span>
                      <NumberInput value={Number(fmt(points[pi][ci]))} onChange={(n) => setCoord(pi, ci, n)} style={{ width: 78 }} />
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12 }}>
            <button onClick={build} disabled={busy}>{busy ? "생성 중…" : "그래스프 USD 생성"}</button>
          </div>
        </div>
      )}

      {resultAsset && (
        <div className="card">
          <label style={{ margin: 0 }}>결과 USD (grasp_identifier_curve)</label>
          <p className="muted">{resultAsset.filename} · {(resultAsset.bytes / 1024).toFixed(1)} KB</p>
          <p className="muted" style={{ fontSize: 12, marginTop: 2 }}>✓ NVIDIA가 grasp로 인식하는 <code>grasp_identifier_curve</code>(2점 BasisCurves)가 defaultPrim 아래에 들어간 자기완결 USD입니다(GSP.001).</p>
          <div className="row" style={{ marginTop: 8 }}><button className="ghost" onClick={() => downloadAsset(resultAsset.download_url, resultAsset.filename)}>USD 다운로드</button></div>
          <SpinViewer assetId={resultAsset.id} label="🖱 인터랙티브 RTX 뷰어" />
        </div>
      )}
    </>
  );
}
