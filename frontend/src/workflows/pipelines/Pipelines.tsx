"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE, blobUrl, CancelledError, downloadAsset, submitAndPoll } from "@/lib/api";
import { authHeaders, canUseCard, getUser } from "@/lib/auth";
import { DEV_OVERRIDE, type WorkflowModuleProps } from "../registry";
import SpinViewer from "../SpinViewer";
import DeliveryReport, { PackageResult } from "../simready-delivery/DeliveryReport";
import type { Classify as DeliveryClassify, PkgInfo, Report as DeliveryReportData } from "../simready-delivery/DeliveryReport";
import NumberInput from "../NumberInput";
import Articulation from "../articulation/Articulation";
import Grasp from "../grasp/Grasp";

// 직접 모드에서 인라인 임베드할 편집기 카드 (manifest.interactive=true 카드와 매칭)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const EMBED_EDITORS: Record<string, any> = { articulation: Articulation, grasp: Grasp };
const EMBED_ACCEPT: Record<string, string> = {
  articulation: ".step,.stp,.usd,.usda,.usdc,.usdz",
  grasp: ".usd,.usda,.usdc,.usdz",
};

type StepInputs = { file: string[]; text: boolean; images: boolean };
type StepParam = { key: string; label: string; type: string; default?: string; options?: (string | [string, string])[] };
type StepSpec = { id: string; name: string; icon: string; cat: string; accepts: string[]; produces: string; terminal: boolean; start?: boolean; dev?: boolean; disabled?: boolean; desc: string; inputs?: StepInputs; params?: StepParam[]; interactive?: boolean };
// optional=건너뛰기 가능 / params=스텝 옵션(예 충돌전략) / mode=interactive 카드의 자동(AI)·직접(멈춰 편집)
type ChainStep = { id: string; optional?: boolean; params?: Record<string, string>; mode?: "auto" | "manual" };
type MatRow = { name: string; size_mm?: number[]; material?: string; mdl?: string; reason?: string };
type PhysRow = { name: string; material?: string; bound_visual_material?: string; density?: number; area_m2?: number; mass_kg?: number; solid_mass_kg?: number; auto_wall_mm?: number; shell_eligible?: boolean; shell_reason?: string; dims_mm?: number[]; volume_method?: string; static_friction?: number; dynamic_friction?: number; restitution?: number; material_reasoning?: string; physics_reasoning?: string };
// package = NVIDIA Sim-Ready Delivery 스텝 — 납품 카드와 같은 검증 UI(DeliveryReport)를 그대로 띄운다.
type PackageResultData = { kind: "package"; profile_used?: string; deliverable?: boolean; downgraded?: boolean;
  remaining_codes?: string[]; report?: DeliveryReportData; classify?: DeliveryClassify | null; package?: PkgInfo };
type StepResult = { kind: "material"; rows: MatRow[]; notes?: string } | { kind: "physics"; parts: PhysRow[]; total_mass_kg?: number; solid_total_mass_kg?: number } | PackageResultData | null;
type ShellMode = "auto" | "solid" | "manual";

// 쉘 질량 — 솔리드 기준값에서 즉시 계산(백엔드 reauthor 와 동일식). 적격(비폐쇄)만 보정, 솔리드 상한.
function shellPartMass(p: PhysRow, mode: ShellMode, mm: number): { mass: number; preview: boolean; t: number } {
  const solid = p.solid_mass_kg ?? p.mass_kg ?? 0;
  if (!p.shell_eligible || !p.area_m2 || !p.density) return { mass: solid, preview: false, t: 0 };
  const t = mode === "solid" ? 0 : mode === "manual" ? mm : (p.auto_wall_mm ?? 0);
  if (!t || t <= 0) return { mass: solid, preview: false, t: 0 };
  return { mass: Math.min(p.density * p.area_m2 * (t / 1000), solid), preview: true, t };
}
const r3 = (v?: number) => (v === undefined || v === null ? "—" : Math.round(v * 1000) / 1000);
// 재질 목록은 백엔드 taxonomy(/mass-physics/materials)에서 받아오고, 실패 시 이 값으로 폴백.
const MAT_FALLBACK = ["steel", "stainless_steel", "aluminum", "brass", "titanium", "abs",
  "polycarbonate", "nylon", "rubber", "glass", "polypropylene", "hdpe"];
type RunStep = { id: string; name: string; status: string; type?: string; type_label?: string; error?: string; asset?: AssetRec | null; result?: StepResult };

// 비디오 미리보기(인증 헤더로 blob 받아 재생)
function VideoView({ url }: { url: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => { let u = ""; blobUrl(url).then((b) => { u = b; setSrc(b); }).catch(() => {}); return () => { if (u) URL.revokeObjectURL(u); }; }, [url]);
  return src ? <video src={src} controls autoPlay loop muted playsInline style={{ width: "100%", borderRadius: 8, background: "#0d1117" }} /> : <p className="muted">로딩…</p>;
}
type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
type Saved = { id: string; title: string; steps: ChainStep[]; formats?: string[]; author?: string };

const FMT_OF = (a: string) => (a === "mesh" ? MESH_EXT : a === "usd" ? USD_EXT : []);

const MESH_EXT = [".step", ".stp", ".stl", ".obj", ".ply", ".glb", ".gltf"];
const USD_EXT = [".usd", ".usda", ".usdc", ".usdz"];
const USD_SUB = ["usd_geom", "usd_material", "usd_physics"];

function extOf(name: string) { const i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); }
function initType(name: string): string | null {
  const e = extOf(name);
  if (MESH_EXT.includes(e)) return "mesh";
  if (USD_EXT.includes(e)) return "usd_geom";
  return null;
}
function compat(produced: string | null, accepts: string[]): boolean {
  if (!produced) return true; // 파일 미선택 → 일단 허용(실행 시 재검증)
  if (accepts.includes(produced)) return true;
  if (USD_SUB.includes(produced) && accepts.includes("usd")) return true;
  if (produced === "usd" && (accepts.includes("usd") || accepts.some((a) => USD_SUB.includes(a)))) return true;
  return false;
}

export default function Pipelines(_props: WorkflowModuleProps) {
  const [catalog, setCatalog] = useState<StepSpec[]>([]);
  const [typeLabel, setTypeLabel] = useState<Record<string, string>>({});
  const [file, setFile] = useState<File | null>(null);
  const [commonText, setCommonText] = useState("");                        // 공통 설명 — text 받는 모든 스텝에 적용
  const [commonImages, setCommonImages] = useState<File[]>([]);            // 공통 참조 이미지
  const [scaleUnits, setScaleUnits] = useState("auto");                    // 입력 단위 강제(auto/m/mm/cm) — 물성 스케일 보정
  const [shellMode, setShellMode] = useState<ShellMode>("auto");           // 쉘 보정 모드(물성 결과)
  const [shellMm, setShellMm] = useState(3);                               // 직접 지정 두께(mm)
  const [dlStep, setDlStep] = useState<number | null>(null);               // 다운로드(재작성) 진행중 스텝
  // 물성 결과의 재질을 사람이 고치는 상태 — AI 추론이 틀렸을 때 부품별로 바꿔 다시 만든다.
  const [matKeys, setMatKeys] = useState<string[]>(MAT_FALLBACK);          // 선택 가능한 재질(백엔드 taxonomy)
  const [matOv, setMatOv] = useState<Record<number, Record<string, string>>>({});  // {스텝: {부품: 재질}}
  const [matBusy, setMatBusy] = useState<number | null>(null);
  const [matDone, setMatDone] = useState<Record<number, boolean>>({});     // 재질 수정 반영됨 → 이후 스텝 재실행 안내
  const [stepText, setStepText] = useState<Record<number, string>>({});    // 스텝별 텍스트 덮어쓰기(체인 인덱스 키)
  const [stepImages, setStepImages] = useState<Record<number, File[]>>({}); // 스텝별 이미지 덮어쓰기
  const [chain, setChain] = useState<ChainStep[]>([]);
  const [skipOpt, setSkipOpt] = useState<Record<number, boolean>>({});  // 실행 시 건너뛸 옵셔널 스텝(체인 인덱스)
  const [saved, setSaved] = useState<Saved[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [view, setView] = useState<"list" | "workspace">("list");  // 카드 목록 ↔ 작업공간
  const [nameInput, setNameInput] = useState<string | null>(null); // null=비저장중, 문자열=이름 입력 중
  const [mode, setMode] = useState<"define" | "run">("define");    // 정의(형식·스텝) ↔ 실행(파일 넣고 돌림)
  const [formats, setFormats] = useState<string[]>([]);            // 이 파이프라인이 받을 입력 확장자
  const [title, setTitle] = useState("");
  const [editingId, setEditingId] = useState("");

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prog, setProg] = useState<{ index?: number; total?: number; label?: string } | null>(null);
  const [result, setResult] = useState<{ ok: boolean; steps: RunStep[]; failed_at?: number } | null>(null);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);  // 클릭한 스텝 결과 펼침
  const acRef = useRef<AbortController | null>(null);
  // 직접 모드 일시정지: 인라인 편집기 + 입력 에셋. null 이면 멈춤 아님.
  const [paused, setPaused] = useState<{ activeIdx: number; cardId: string; inputFile: File } | null>(null);
  const accStepsRef = useRef<RunStep[]>([]);                                  // 분절 실행 누적 결과
  const runActiveRef = useRef<{ s: ChainStep; i: number }[]>([]);             // 이번 실행의 (스킵 제외) 스텝들

  const spec = (id: string) => catalog.find((s) => s.id === id);
  const fileType = file ? initType(file.name) : null;
  // 체인 끝의 출력 타입(없으면 입력 타입)
  const tailType = chain.length ? (spec(chain[chain.length - 1].id)?.produces ?? null) : fileType;
  const tailTerminal = chain.length ? !!spec(chain[chain.length - 1].id)?.terminal : false;

  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/workflows/pipelines/steps`, { headers: authHeaders(), cache: "no-store" });
        const j = await r.json();
        setCatalog(j.steps ?? []); setTypeLabel(j.type_label ?? {});
      } catch { /* */ }
      try {
        const r = await fetch(`${API_BASE}/api/workflows/pipelines/defs`, { headers: authHeaders(), cache: "no-store" });
        const j = await r.json(); setSaved(j.pipelines ?? []);
      } catch { /* */ }
    })();
  }, []);

  function addStep(id: string) { setChain((c) => [...c, { id }]); setShowAdd(false); }
  function removeStep(i: number) { setChain((c) => c.filter((_, k) => k !== i)); }

  const chips = (() => {  // 입력 + 각 스텝 출력 = 흐르는 산출물 칩
    const out: string[] = [];
    out.push(fileType ? (typeLabel[fileType] ?? fileType) : "입력");
    let t = fileType;
    for (const s of chain) { const sp = spec(s.id); t = sp?.produces ?? t; out.push(typeLabel[t ?? ""] ?? t ?? "?"); }
    return out;
  })();
  // 선형 파이프라인 → 입력은 '첫 스텝'의 입력 스펙을 그대로 따라간다.
  const firstSpec = chain.length ? spec(chain[0].id) : undefined;
  const firstStart = !!firstSpec?.start;                                   // 텍스트/이미지로 시작(파일 없음)
  const needsFile = (firstSpec?.inputs?.file?.length ?? 0) > 0;            // 첫 스텝이 파일을 받나
  const needsText = !!firstSpec?.inputs?.text;
  const needsImages = !!firstSpec?.inputs?.images;
  function loadExample() {
    setChain([{ id: "asset-prep" }, { id: "material-usd" }, { id: "mass-physics" }, { id: "turntable" }]);
    setResult(null);
  }
  function resetInputs() { setResult(null); setFile(null); setCommonText(""); setCommonImages([]); setStepText({}); setStepImages({}); setSkipOpt({}); setError(null); setProg(null); setPaused(null); accStepsRef.current = []; }
  function openPipeline(s: Saved) { setChain(s.steps.map((x) => ({ id: x.id, optional: x.optional, params: x.params, mode: x.mode }))); setTitle(s.title); setEditingId(s.id); setFormats(s.formats ?? []); resetInputs(); setMode("run"); setView("workspace"); }
  function newPipeline() { setChain([]); setTitle(""); setEditingId(""); setFormats([]); resetInputs(); setMode("define"); setView("workspace"); }

  // 입력 형식: 첫 스텝의 accepts 로 가능한 확장자 도출. 선택 안 했으면(formats 빈배열) 전체 허용.
  const firstAccepts = chain.length ? (spec(chain[0].id)?.accepts ?? []) : [];
  const availFormats = Array.from(new Set(firstAccepts.flatMap(FMT_OF)));
  const allowedFormats = formats.length ? formats.filter((f) => availFormats.includes(f)) : availFormats;
  const runAccept = (allowedFormats.length ? allowedFormats : [...MESH_EXT, ...USD_EXT]).join(",");
  function toggleFmt(ext: string) {
    setFormats((prev) => {
      const base = prev.length ? prev.filter((f) => availFormats.includes(f)) : availFormats;
      return base.includes(ext) ? base.filter((x) => x !== ext) : [...base, ext];
    });
  }
  async function refreshSaved() {
    try { const j = await fetch(`${API_BASE}/api/workflows/pipelines/defs`, { headers: authHeaders(), cache: "no-store" }); setSaved((await j.json()).pipelines ?? []); } catch { /* */ }
  }
  function backToList() { setView("list"); refreshSaved(); }

  const isManualStep = (s: ChainStep) => !!(spec(s.id)?.interactive && s.mode === "manual");

  // 중간 산출 에셋 → File (다음 구간/직접 스텝 입력으로 재사용)
  async function fileFromAsset(a: AssetRec): Promise<File> {
    const res = await fetch(`${API_BASE}${a.download_url}`, { headers: authHeaders() });
    if (!res.ok) throw new Error(`중간 산출물 로드 실패 (${res.status})`);
    const blob = await res.blob();
    return new File([blob], a.filename, { type: blob.type || "application/octet-stream" });
  }

  // 연속 auto 스텝 한 구간을 run-submit 으로 실행
  function runSegment(seg: { s: ChainStep; i: number }[], inputFile: File, ac: AbortController) {
    const fd = new FormData();
    fd.append("file", inputFile);
    if (commonText.trim()) fd.append("text", commonText.trim());
    if (scaleUnits !== "auto") fd.append("scale_units", scaleUnits);
    for (const im of commonImages) fd.append("images", im);
    fd.append("steps", JSON.stringify(seg.map(({ s, i }) => {
      const sp = spec(s.id);
      const params: Record<string, unknown> = { ...(s.params ?? {}) };  // 스텝 옵션(예: collision)
      const t = (stepText[i] ?? "").trim();
      if (sp?.inputs?.text && t) params.text = t;
      return { id: s.id, params };
    })));
    return submitAndPoll<{ ok: boolean; steps: RunStep[]; failed_at?: number; final_asset?: AssetRec }>(
      `/api/workflows/pipelines/run-submit`, fd, { signal: ac.signal, onProgress: setProg });
  }

  // 분절 실행: activeIdx 부터 auto 구간을 돌리고, '직접' 스텝을 만나면 멈춰 인라인 편집기를 띄운다.
  async function advance(activeIdx: number, inputFile: File | null) {
    const active = runActiveRef.current;
    const ac = acRef.current ?? new AbortController(); acRef.current = ac;
    if (activeIdx >= active.length) { setBusy(false); return; }   // 완료
    let j = activeIdx;
    while (j < active.length && !isManualStep(active[j].s)) j++;   // 연속 auto 구간 [activeIdx, j)
    let input = inputFile;
    try {
      if (j > activeIdx) {
        if (!input) throw new Error("입력 파일이 없습니다.");
        const seg = active.slice(activeIdx, j);
        const r = await runSegment(seg, input, ac);
        seg.forEach(({ i }, k) => { accStepsRef.current[i] = r.steps[k]; });   // 체인 인덱스에 결과 배치
        setResult({ ok: r.ok, steps: [...accStepsRef.current], failed_at: r.failed_at });
        if (!r.ok) { setBusy(false); return; }
        const last = r.final_asset ?? [...r.steps].reverse().find((x) => x.asset)?.asset;
        if (!last) throw new Error("이전 구간 산출물을 찾지 못했습니다.");
        input = await fileFromAsset(last);
      }
    } catch (e) {
      setError(e instanceof CancelledError ? "취소되었습니다." : String((e as Error).message));
      setBusy(false); return;
    }
    if (j < active.length) { setPaused({ activeIdx: j, cardId: active[j].s.id, inputFile: input as File }); setBusy(false); }
    else { setBusy(false); }   // 완료
  }

  // 직접 스텝 편집 완료(임베드 편집기의 onComplete) → 그 스텝 결과 기록 후 다음 구간 재개
  async function onManualComplete(asset: AssetRec) {
    const p = paused; if (!p) return;
    const st = runActiveRef.current[p.activeIdx]; const sp = spec(st.s.id);
    accStepsRef.current[st.i] = { id: st.s.id, name: sp?.name ?? st.s.id, status: "done", asset,
      type: sp?.produces, type_label: typeLabel[sp?.produces ?? ""] ?? sp?.produces };
    setResult({ ok: true, steps: [...accStepsRef.current] });
    setPaused(null); setBusy(true); acRef.current = new AbortController();
    let input: File;
    try { input = await fileFromAsset(asset); }
    catch (e) { setError(String((e as Error).message)); setBusy(false); return; }
    advance(p.activeIdx + 1, input);
  }

  function run() {
    setError(null); setResult(null); setProg(null); setExpandedStep(null); setPaused(null);
    if (!chain.length) { setError("스텝을 1개 이상 추가하세요."); return; }
    if (needsFile && !file) { setError("입력 파일을 선택하세요."); return; }
    const active = chain.map((s, i) => ({ s, i })).filter(({ s, i }) => !(s.optional && skipOpt[i]));
    if (!active.length) { setError("모든 스텝이 건너뛰기됨 — 최소 1개는 실행해야 합니다."); return; }
    runActiveRef.current = active; accStepsRef.current = new Array(chain.length).fill(undefined) as RunStep[];
    acRef.current = new AbortController(); setBusy(true); setShellMode("auto");
    advance(0, file);
  }

  // 물성 USD 다운로드 — 현재 쉘 모드/두께로 그 자리에서 재작성(reauthor) 후 받기. LLM·메시 재계산 없음.
  async function downloadPhysics(stepIdx: number, asset: AssetRec) {
    setDlStep(stepIdx); setError(null);
    try {
      const fd = new FormData();
      fd.append("asset_id", asset.id);
      fd.append("shell_mm", String(shellMode === "auto" ? -1 : shellMode === "solid" ? 0 : shellMm));
      const res = await fetch(`${API_BASE}/api/workflows/mass-physics/shell-reauthor`, { method: "POST", headers: authHeaders(), body: fd });
      if (!res.ok) throw new Error(`reauthor ${res.status}`);
      const r = (await res.json()) as { asset: AssetRec };
      await downloadAsset(r.asset.download_url, r.asset.filename);
    } catch (e) { setError(`USD 생성 실패: ${String((e as Error).message)}`); }
    finally { setDlStep(null); }
  }

  // 재질 목록(백엔드 taxonomy) 한 번 로드
  useEffect(() => {
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/workflows/mass-physics/materials`, { headers: authHeaders() });
        const j = (await r.json()) as { materials?: Record<string, unknown> };
        const k = Object.keys(j.materials ?? {});
        if (k.length) setMatKeys(k);
      } catch { /* 폴백 사용 */ }
    })();
  }, []);

  // AI 재질 추론이 틀렸을 때 — 부품별 재질을 사람이 지정한 값으로 바꿔 즉시 재작성(LLM 미사용).
  // 질량=밀도×부피로 다시 계산되고, 룩(시각재질·UV·텍스처)은 그대로 보존된다.
  async function applyMaterials(stepIdx: number, asset: AssetRec) {
    const ov = matOv[stepIdx] ?? {};
    if (!Object.keys(ov).length) return;
    setMatBusy(stepIdx); setError(null);
    try {
      const fd = new FormData();
      fd.append("asset_id", asset.id);
      fd.append("overrides", JSON.stringify(ov));
      fd.append("collision", chain[stepIdx]?.params?.collision ?? "convexHull");
      const res = await fetch(`${API_BASE}/api/workflows/mass-physics/material-reauthor`,
        { method: "POST", headers: authHeaders(), body: fd });
      if (!res.ok) throw new Error(`${res.status} ${(await res.text()).slice(0, 180)}`);
      const j = (await res.json()) as {
        asset: AssetRec;
        applied: { name: string; material: string; density: number; mass_kg: number; source: string }[];
        total_mass_kg: number;
      };
      setResult((r) => {
        if (!r) return r;
        const steps = r.steps.map((s, k) => {
          if (k !== stepIdx) return s;
          const res2 = s.result && s.result.kind === "physics"
            ? {
                ...s.result,
                total_mass_kg: j.total_mass_kg,
                parts: s.result.parts.map((p) => {
                  const a = j.applied.find((x) => x.name === p.name);
                  return a ? { ...p, material: a.material, density: a.density, mass_kg: a.mass_kg,
                               solid_mass_kg: a.mass_kg, material_reasoning: a.source } : p;
                }),
              }
            : s.result;
          return { ...s, asset: j.asset, result: res2 };
        });
        return { ...r, steps };
      });
      const prev = accStepsRef.current[stepIdx];
      if (prev) accStepsRef.current[stepIdx] = { ...prev, asset: j.asset };
      setMatOv((m) => ({ ...m, [stepIdx]: {} }));
      setMatDone((m) => ({ ...m, [stepIdx]: true }));
    } catch (e) { setError(`재질 재작성 실패: ${String((e as Error).message)}`); }
    finally { setMatBusy(null); }
  }

  // 재질을 고친 뒤 그 다음 스텝부터 다시 실행(앞 스텝은 재실행하지 않음).
  async function rerunAfter(stepIdx: number, asset: AssetRec) {
    const active = runActiveRef.current;
    const pos = active.findIndex((a) => a.i === stepIdx);
    if (pos < 0) { setError("이 스텝이 이번 실행에 없어 이어서 돌릴 수 없습니다."); return; }
    setError(null); setMatDone((m) => ({ ...m, [stepIdx]: false }));
    try {
      const f = await fileFromAsset(asset);
      acRef.current = new AbortController(); setBusy(true);
      advance(pos + 1, f);
    } catch (e) { setError(String((e as Error).message)); setBusy(false); }
  }

  function startSave() { setNameInput(title || "내 파이프라인"); }     // 인라인 이름 입력 열기
  async function confirmSave() {
    const t = (nameInput ?? "").trim();
    if (!t) return;
    const r = await fetch(`${API_BASE}/api/workflows/pipelines/defs`, {
      method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify({ id: editingId || "", title: t, steps: chain, formats }),
    });
    if (r.ok) {
      const rec = await r.json();
      setEditingId(rec.id); setTitle(rec.title);
      setNameInput(null);
      await refreshSaved();
      setView("list");          // 저장 후 → 파이프라인 목록으로
    }
  }

  async function deleteDef(id: string) {
    if (!confirm("이 파이프라인을 삭제할까요?")) return;
    await fetch(`${API_BASE}/api/workflows/pipelines/defs/${id}`, { method: "DELETE", headers: authHeaders() });
    const j = await fetch(`${API_BASE}/api/workflows/pipelines/defs`, { headers: authHeaders() });
    setSaved((await j.json()).pipelines ?? []);
  }

  // 실행 중/완료 시 스텝 상태 계산
  const stepState = (i: number): "done" | "run" | "wait" | "fail" => {
    if (result) {
      const rs = result.steps[i];
      if (rs) return rs.status === "done" ? "done" : "fail";
      return "wait";
    }
    if (busy && prog?.index) { if (i + 1 < prog.index) return "done"; if (i + 1 === prog.index) return "run"; }
    return "wait";
  };

  // ── 카드 목록 화면 ── 작성자(아이디)별 그룹 + 새 파이프라인 카드
  if (view === "list") {
    const authors = Array.from(new Set(saved.map((s) => s.author || "기타"))).sort();
    const me = getUser();
    const card = (s: Saved) => {
      // 스텝 중 하나라도 권한 없는 카드면 파이프라인 전체를 잠금.
      const blocked = s.steps.filter((x) => !canUseCard(me, x.id)).map((x) => spec(x.id)?.name ?? x.id);
      const locked = blocked.length > 0;
      return (
      <div key={s.id} className="wf-card" role="button" tabIndex={0}
        onClick={() => { if (locked) { setError(`이 파이프라인엔 권한 없는 카드가 있어 열 수 없습니다: ${blocked.join(", ")}`); } else openPipeline(s); }}
        onKeyDown={(e) => { if (e.key === "Enter" && !locked) openPipeline(s); }}
        style={{ position: "relative", cursor: "pointer", opacity: locked ? 0.6 : 1 }}>
        <div className="wf-icon">🔗</div>
        <div className="wf-name">{s.title}</div>
        <div className="wf-desc">{s.steps.map((x) => spec(x.id)?.icon ?? "▢").join(" ")} · {s.steps.length}스텝</div>
        {locked && <div className="wf-lock">🔒 권한 없는 카드 포함</div>}
        <button className="ghost" title="삭제" style={{ position: "absolute", top: 8, right: 8, padding: "0 8px", fontSize: 12 }}
          onClick={(e) => { e.stopPropagation(); deleteDef(s.id); }}>✕</button>
      </div>
      );
    };
    return (
      <div className="cats">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ margin: 0 }}>자동화 파이프라인</h2>
          <button onClick={newPipeline}>＋ 새 파이프라인</button>
        </div>
        {saved.length === 0 && <p className="muted">아직 만든 파이프라인이 없어요 — <b>＋ 새 파이프라인</b>으로 시작하세요.</p>}
        {authors.map((a) => (
          <section className="cat" key={a}>
            <h3 className="cat-title">{a}</h3>
            <div className="grid">{saved.filter((s) => (s.author || "기타") === a).map(card)}</div>
          </section>
        ))}
      </div>
    );
  }

  // ── 작업공간 ── 정의(define: 형식·스텝 정하고 저장) ↔ 실행(run: 파일 넣고 돌림)
  // 스텝 추가 메뉴: 개발중(dev)·비활성(disabled)·권한 없는 카드는 숨긴다.
  const _me = getUser();
  const addable = catalog.filter((c) => !c.dev && !c.disabled && !DEV_OVERRIDE.has(c.id) && canUseCard(_me, c.id));
  const catOrder = Array.from(new Set(addable.map((c) => c.cat)));
  return (
    <div className="wf-body">
      <div className="card">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <label style={{ margin: 0 }}>{mode === "run" ? `▶ ${title || "파이프라인"}` : (editingId ? `✎ ${title}` : "새 파이프라인 정의")}</label>
          <span className="row" style={{ gap: 6 }}>
            {mode === "run" && <button className="ghost" onClick={() => { setMode("define"); resetInputs(); }}>✎ 편집</button>}
            <button className="ghost" onClick={backToList}>← 내 파이프라인</button>
          </span>
        </div>
        <p className="muted">{mode === "run"
          ? "파일을 넣고 실행하세요. 이전 스텝 산출물이 다음 입력으로 자동 전달됩니다."
          : "받을 입력 형식과 스텝을 정해 저장하세요. 실제 파일은 저장 후 카드로 실행할 때 넣습니다."}</p>

        {/* 입력 영역 */}
        {mode === "run" ? (
          <>
            {/* (1) 입력(공통) — 파일 + text/images 받는 모든 스텝에 적용되는 공통 설명·이미지를 한 곳에서. */}
            {(() => {
              const anyText = chain.some((s) => spec(s.id)?.inputs?.text);
              const anyImages = chain.some((s) => spec(s.id)?.inputs?.images);
              return (
                <div className="card-sub" style={{ marginTop: 4 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>입력 {anyText || anyImages ? <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>· 설명·이미지는 필요한 모든 스텝에 공통 적용</span> : null}</div>
                  {!firstStart && needsFile && (<>
                    <label style={{ marginTop: 0 }}>입력 파일 ({allowedFormats.join(" / ") || "형식 미지정"})</label>
                    <input type="file" accept={runAccept} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} />
                    {file && <p className="muted" style={{ fontSize: 12 }}>입력 타입: <b>{typeLabel[fileType ?? ""] ?? "알 수 없음"}</b>
                      {!fileType && " — 지원 안 하는 확장자"}
                      {fileType && allowedFormats.length > 0 && !allowedFormats.includes(extOf(file.name)) && <span className="err"> · 이 파이프라인이 받는 형식이 아닙니다</span>}</p>}
                  </>)}
                  {firstStart && <p className="muted" style={{ fontSize: 12, margin: "0 0 2px" }}>📝 입력 파일 없이 아래 설명{anyImages ? "·이미지" : ""}로 시작합니다.</p>}
                  {!firstStart && needsFile && (
                    <div style={{ marginTop: 8 }}>
                      <label style={{ margin: 0 }}>입력 단위 (스케일)<span className="muted" style={{ fontWeight: 400, fontSize: 11 }}> · 질량이 비현실적이면 단위를 강제하세요</span></label>
                      <select value={scaleUnits} onChange={(e) => setScaleUnits(e.target.value)} style={{ maxWidth: 280 }}>
                        <option value="auto">자동 (파일 단위 사용)</option>
                        <option value="m">m (미터)</option>
                        <option value="mm">mm (밀리미터)</option>
                        <option value="cm">cm (센티미터)</option>
                      </select>
                      <p className="muted" style={{ fontSize: 11, margin: "2px 0 0" }}>
                        자동: USD는 파일의 metersPerUnit, 메시(STEP/STL)는 m로 해석. 질량이 너무 크/작으면 실제 단위로 강제(물성 추론에 적용). 속 빈 형상의 질량 과대 보정은 <b>물성 결과에서</b> AI 자동/슬라이더로 조정합니다.
                      </p>
                    </div>
                  )}
                  {anyText && (<>
                    <label style={{ marginTop: (!firstStart && needsFile) ? 8 : 0 }}>{firstStart ? "설명 / 프롬프트" : "공통 설명 (선택)"}</label>
                    <textarea rows={2} value={commonText} onChange={(e) => setCommonText(e.target.value)}
                      placeholder={firstStart ? "예: 가로 100mm 박스, 윗면 원통 손잡이" : "예: 한국전력 변전소 GIS, 강철 외함 + 알루미늄 프레임"} />
                  </>)}
                  {anyImages && (<>
                    <label style={{ marginTop: 8 }}>공통 참조 이미지 (선택 · 복수)</label>
                    <input type="file" accept="image/*" multiple onChange={(e) => setCommonImages(Array.from(e.target.files ?? []))} />
                    {commonImages.length > 0 && <span className="muted" style={{ fontSize: 12 }}> {commonImages.length}장 첨부됨</span>}
                  </>)}
                </div>
              );
            })()}

            {/* (2) 스텝별로 다르게 줄 것만(덮어쓰기) — 비우면 위 공통 입력을 사용. 이전 스텝 산출물은 자동 전달. */}
            {chain.some((s, i) => i > 0 && (spec(s.id)?.inputs?.text || spec(s.id)?.inputs?.images)) && (
              <div className="muted" style={{ fontSize: 12, margin: "12px 0 2px" }}>스텝별로 다르게 줄 것만 (선택 · 비우면 위 공통 입력 사용)</div>
            )}
            {chain.map((s, i) => {
              if (i === 0) return null;
              const sp = spec(s.id); if (!sp) return null;
              const wantsT = !!sp.inputs?.text, wantsI = !!sp.inputs?.images;
              if (!wantsT && !wantsI) return null;
              return (
                <div key={i} className="card-sub" style={{ marginTop: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>스텝 {i + 1} · {sp.icon} {sp.name} <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>· 덮어쓰기</span></div>
                  {wantsT && (<>
                    <label style={{ marginTop: 4 }}>설명 (비우면 공통 사용)</label>
                    <textarea rows={2} value={stepText[i] ?? ""} onChange={(e) => setStepText((m) => ({ ...m, [i]: e.target.value }))}
                      placeholder="이 스텝만 다른 설명을 줄 때" />
                  </>)}
                  {wantsI && (<>
                    <label style={{ marginTop: 6 }}>참조 이미지 (비우면 공통 사용)</label>
                    <input type="file" accept="image/*" multiple onChange={(e) => setStepImages((m) => ({ ...m, [i]: Array.from(e.target.files ?? []) }))} />
                    {(stepImages[i]?.length ?? 0) > 0 && <span className="muted" style={{ fontSize: 12 }}> {stepImages[i].length}장 첨부됨</span>}
                  </>)}
                </div>
              );
            })}
          </>
        ) : (
          <>
            <label>입력</label>
            {chain.length === 0
              ? <p className="muted" style={{ fontSize: 13 }}>첫 스텝을 추가하면 입력 방식(파일 형식 또는 텍스트/이미지)이 정해집니다.</p>
              : firstStart
                ? <p className="muted" style={{ fontSize: 13 }}>📝 텍스트{needsImages ? "·이미지" : ""}로 시작 — 실행 때 입력합니다(파일 없음).</p>
                : (
                  <>
                    <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>받을 파일 형식 (체크한 것만 실행 때 업로드 가능)</div>
                    <div className="row" style={{ flexWrap: "wrap", gap: 12 }}>
                      {availFormats.map((ext) => (
                        <label key={ext} style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, margin: 0 }}>
                          <input type="checkbox" checked={allowedFormats.includes(ext)} onChange={() => toggleFmt(ext)} style={{ width: "auto" }} /> {ext}
                        </label>
                      ))}
                    </div>
                  </>
                )}
            {/* 스텝 옵션(예: 충돌 전략) + 자동/직접 모드 — interactive 카드는 직접 모드 시 실행 중 멈춰 편집 */}
            {chain.some((s) => ((spec(s.id)?.params?.length ?? 0) > 0) || spec(s.id)?.interactive) && (
              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>스텝 옵션 · 모드</div>
                {chain.map((s, i) => {
                  const sp = spec(s.id); if (!sp) return null;
                  if ((sp.params?.length ?? 0) === 0 && !sp.interactive) return null;
                  return (
                    <div key={i} className="card-sub" style={{ marginTop: 6 }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>스텝 {i + 1} · {sp.icon} {sp.name}</div>
                      {sp.interactive && (
                        <label style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, fontSize: 12, marginTop: 4 }}>
                          모드
                          <select value={s.mode ?? "auto"} style={{ width: "auto" }}
                            onChange={(e) => setChain((c) => c.map((x, k) => (k === i ? { ...x, mode: e.target.value as "auto" | "manual" } : x)))}>
                            <option value="auto">자동 (AI 추론 · 멈춤 없음)</option>
                            <option value="manual">직접 (실행 중 멈춰 편집)</option>
                          </select>
                        </label>
                      )}
                      {(sp.params ?? []).map((p) => (
                        <label key={p.key} style={{ display: "flex", gap: 6, alignItems: "center", fontWeight: 400, fontSize: 12, marginTop: 4 }}>
                          {p.label}
                          <select value={s.params?.[p.key] ?? p.default ?? ""} style={{ width: "auto" }}
                            onChange={(e) => setChain((c) => c.map((x, k) => (k === i ? { ...x, params: { ...(x.params ?? {}), [p.key]: e.target.value } } : x)))}>
                            {(p.options ?? []).map((o) => {
                              const [val, lbl] = Array.isArray(o) ? o : [o, o];
                              return <option key={val} value={val}>{lbl}</option>;
                            })}
                          </select>
                        </label>
                      ))}
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* 가이드(정의·빈 체인) */}
        {mode === "define" && chain.length === 0 && (
          <div className="card-sub" style={{ marginTop: 12 }}>
            <b>시작하기</b>
            <ol style={{ margin: "6px 0 10px 18px", fontSize: 13, lineHeight: 1.8 }}>
              <li><b>+ 스텝 추가</b>로 카드(블록)를 쌓아요 — <b>타입이 맞는 카드만</b> 활성(안 맞으면 🚫 회색), 카테고리별로 묶여 나와요.</li>
              <li>맨 위에서 <b>받을 입력 형식</b>을 정해요(첫 스텝 기준).</li>
              <li><b>저장</b>하면 카드로 남고, 그 카드로 들어가 파일 넣고 <b>▶ 실행</b>해요.</li>
            </ol>
            <button className="ghost" onClick={loadExample}>예시 불러오기 ▸ 정리 → 재질 → 물성 → 영상</button>
          </div>
        )}

        {/* 체인(공통) */}
        <div style={{ marginTop: 14 }}>
          <div className="muted" style={{ fontSize: 13, marginBottom: 6 }}>📎 입력 → {chips.join(" → ")}</div>
          {chain.map((s, i) => {
            const sp = spec(s.id); if (!sp) return null;
            const stt = stepState(i);
            const col = stt === "done" ? "#4ec9b0" : stt === "run" ? "#d7ba7d" : stt === "fail" ? "#f48771" : "var(--vsc-border-2)";
            const rs = result?.steps[i];
            const hasResult = !!rs?.asset;
            const open = expandedStep === i;
            return (
              <div key={i}>
                <div style={{ textAlign: "center", color: "#4ec9b0", fontSize: 11 }}>▼ <span className="muted">{chips[i]}</span></div>
                <div className="card-sub" style={{ borderLeft: `3px solid ${col}`, display: "flex", justifyContent: "space-between", alignItems: "center", cursor: hasResult ? "pointer" : "default" }}
                  onClick={hasResult ? () => setExpandedStep(open ? null : i) : undefined}
                  title={hasResult ? "클릭하면 이 단계 결과창이 펼쳐집니다" : undefined}>
                  <div>
                    <b>{hasResult ? (open ? "▾ " : "▸ ") : ""}{i + 1}. {sp.icon} {sp.name}</b> <span className="muted" style={{ fontSize: 12 }}>({sp.cat})</span>
                    {s.optional && <span style={{ fontSize: 11, marginLeft: 6, padding: "1px 6px", borderRadius: 4, background: "var(--vsc-border-2)", color: "var(--vsc-fg)" }}>{mode === "run" && skipOpt[i] ? "건너뜀" : "옵션"}</span>}
                    <div className="muted" style={{ fontSize: 12 }}>받음 {sp.accepts.map((a) => typeLabel[a] ?? a).join("/")} → 냄 {typeLabel[sp.produces] ?? sp.produces}
                      {stt === "run" && " · ⏳ 실행 중…"}{stt === "done" && " · ✓ (클릭=결과 보기)"}{stt === "fail" && " · ✗ 실패"}</div>
                    {rs?.error && <div className="err" style={{ fontSize: 12 }}>{rs.error}</div>}
                  </div>
                  <div className="row" style={{ flex: "none", gap: 8 }} onClick={(e) => e.stopPropagation()}>
                    {mode === "define" && (
                      <label style={{ display: "flex", gap: 4, alignItems: "center", fontWeight: 400, fontSize: 12, margin: 0 }} title="옵셔널 스텝으로 표시 — 실행할 때 건너뛸 수 있게 됩니다">
                        <input type="checkbox" checked={!!s.optional} style={{ width: "auto" }}
                          onChange={(e) => setChain((c) => c.map((x, k) => (k === i ? { ...x, optional: e.target.checked } : x)))} />옵션
                      </label>
                    )}
                    {mode === "run" && s.optional && !result && (
                      <label style={{ display: "flex", gap: 4, alignItems: "center", fontWeight: 400, fontSize: 12, margin: 0 }} title="이번 실행에서 이 옵셔널 스텝 건너뛰기">
                        <input type="checkbox" checked={!!skipOpt[i]} style={{ width: "auto" }}
                          onChange={(e) => setSkipOpt((m) => ({ ...m, [i]: e.target.checked }))} />건너뛰기
                      </label>
                    )}
                    {rs?.asset && rs.type === "usd_physics" && rs.result?.kind === "physics" ? (
                      <button className="ghost" style={{ padding: "2px 10px", fontSize: 12 }} disabled={dlStep === i}
                        onClick={() => downloadPhysics(i, rs.asset!)}>{dlStep === i ? "생성 중…" : `⬇ ${rs.type_label} (${shellMode === "auto" ? "AI두께" : shellMode === "solid" ? "솔리드" : `${shellMm}mm`})`}</button>
                    ) : rs?.asset && (
                      <button className="ghost" style={{ padding: "2px 10px", fontSize: 12 }}
                        onClick={() => downloadAsset(rs.asset!.download_url, rs.asset!.filename)}>⬇ {rs.type_label}</button>
                    )}
                    {mode === "define" && <button className="ghost" style={{ padding: "2px 8px" }} onClick={() => removeStep(i)}>✕</button>}
                  </div>
                </div>
                {open && rs?.asset && (
                  <div className="card-sub" style={{ margin: "4px 0 4px 12px", background: "var(--vsc-panel)" }}>
                    {rs.type === "video"
                      ? <VideoView url={rs.asset.download_url} />
                      : rs.result?.kind === "package"
                      // 납품 스텝: RTX 뷰어(zip 은 스테이지로 못 엶) 대신 카드와 같은 검증·패키지 UI.
                      ? (() => {
                          const pr = rs.result as PackageResultData;
                          const at = rs.asset!;
                          return (
                            <>
                              {pr.report
                                ? <DeliveryReport report={pr.report} classify={pr.classify ?? null} />
                                : <p className="muted" style={{ fontSize: 12, margin: 0 }}>
                                    검증 상세가 없습니다 — 이전 버전으로 실행된 결과입니다. 다시 실행하면 검증 스텝·미충족 항목이 표시됩니다.
                                    {pr.remaining_codes?.length ? <> (미충족: <span style={{ fontFamily: "monospace" }}>{pr.remaining_codes.join(", ")}</span>)</> : null}
                                  </p>}
                              {pr.package && (
                                <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--vsc-border)" }}>
                                  <PackageResult pkg={pr.package} filename={at.filename} bytes={at.bytes}
                                    onDownload={() => downloadAsset(at.download_url, at.filename)} />
                                </div>
                              )}
                            </>
                          );
                        })()
                      : <SpinViewer assetId={rs.asset.id} label="🖱 인터랙티브 RTX 뷰어 (드래그·휠)" />}
                    {rs.result?.kind === "material" && (
                      <div style={{ overflowX: "auto", marginTop: 8 }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                          <thead><tr style={{ textAlign: "left", borderBottom: "2px solid var(--vsc-border)" }}>
                            <th style={{ padding: "4px 6px" }}>부품</th><th style={{ padding: "4px 6px" }}>재질</th><th style={{ padding: "4px 6px" }}>AI 선정 이유</th></tr></thead>
                          <tbody>{rs.result.rows.map((r, k) => (
                            <tr key={k} style={{ borderBottom: "1px solid var(--vsc-border)" }} title={r.mdl}>
                              <td style={{ padding: "4px 6px", fontWeight: 600 }}>{r.name}</td>
                              <td style={{ padding: "4px 6px" }}>{r.material}</td>
                              <td style={{ padding: "4px 6px", maxWidth: 320 }} className="muted">{r.reason || "—"}</td>
                            </tr>))}</tbody>
                        </table>
                      </div>
                    )}
                    {rs.result?.kind === "physics" && (() => {
                      const parts = rs.result.parts;
                      const pv = parts.map((p) => shellPartMass(p, shellMode, shellMm));
                      const dispTotal = pv.reduce((s, d) => s + (d.mass ?? 0), 0);
                      const anyEligible = parts.some((p) => p.shell_eligible);
                      const solidTotal = rs.result.solid_total_mass_kg;
                      return (
                      <div style={{ overflowX: "auto", marginTop: 8 }}>
                        {anyEligible && (
                          <div style={{ padding: "8px 10px", marginBottom: 6, background: "var(--vsc-bg-subtle,#f3f6f4)", borderRadius: 6 }}>
                            <div className="row" style={{ alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                              <span style={{ fontSize: 12.5, fontWeight: 600 }}>🧪 속 빈 형상 질량 보정</span>
                              <label style={{ margin: 0, fontSize: 12.5 }}><input type="radio" checked={shellMode === "auto"} onChange={() => setShellMode("auto")} /> AI 자동(부품별 벽두께)</label>
                              <label style={{ margin: 0, fontSize: 12.5 }}><input type="radio" checked={shellMode === "manual"} onChange={() => setShellMode("manual")} /> 직접 지정</label>
                              <label style={{ margin: 0, fontSize: 12.5 }}><input type="radio" checked={shellMode === "solid"} onChange={() => setShellMode("solid")} /> 솔리드</label>
                              {shellMode === "manual" && (
                                <span className="row" style={{ alignItems: "center", gap: 6 }}>
                                  <input type="range" min={0.5} max={10} step={0.5} value={shellMm} onChange={(e) => setShellMm(Number(e.target.value))} style={{ width: 120 }} />
                                  <NumberInput min={0} max={50} step={0.5} value={shellMm} onChange={(n) => setShellMm(n)} style={{ width: 60 }} /> mm
                                </span>
                              )}
                            </div>
                            <p className="muted" style={{ fontSize: 11, margin: "4px 0 0" }}>
                              {shellMode === "auto" ? "LLM이 총질량을 보고 부품마다 현실적 벽두께를 정한 값입니다. 다운로드 USD에 그대로 반영됩니다."
                                : shellMode === "manual" ? "비폐쇄 부품 전부에 같은 벽두께 적용. 슬라이더를 움직이면 표·다운로드가 즉시 바뀝니다."
                                : "보정 없이 솔리드(꽉 찬) 가정 질량입니다."}
                            </p>
                          </div>
                        )}
                        <p className="muted" style={{ fontSize: 12, margin: "0 0 4px" }}>
                          총 질량 <b style={{ color: "var(--vsc-accent,#2e7d32)" }}>{r3(dispTotal)} kg</b>
                          {solidTotal && Math.abs(solidTotal - dispTotal) > 0.5 ? <> (솔리드 가정 시 {solidTotal} kg)</> : null}
                        </p>
                        {/* AI 재질 추론 교정 — 표의 '재질'을 바꾼 뒤 적용하면 질량이 다시 계산된다(룩 보존). */}
                        <div className="row" style={{ gap: 8, alignItems: "center", flexWrap: "wrap", margin: "0 0 6px" }}>
                          <span style={{ fontSize: 12.5, fontWeight: 600 }}>🧱 재질 교정</span>
                          <span className="muted" style={{ fontSize: 11 }}>
                            AI 추론이 틀리면 아래 표의 <b>재질</b>을 바꾸고 적용하세요 — 질량=밀도×부피로 다시 계산됩니다(LLM 미사용).
                          </span>
                          {rs.asset && (
                            <button disabled={matBusy === i || busy || !Object.keys(matOv[i] ?? {}).length}
                              onClick={() => applyMaterials(i, rs.asset!)} style={{ fontSize: 12 }}>
                              {matBusy === i ? "적용 중…" : `✔ 재질 적용 (${Object.keys(matOv[i] ?? {}).length}건)`}
                            </button>
                          )}
                          {Object.keys(matOv[i] ?? {}).length > 0 && (
                            <button className="ghost" style={{ fontSize: 12, padding: "2px 8px" }}
                              onClick={() => setMatOv((m) => ({ ...m, [i]: {} }))}>되돌리기</button>
                          )}
                        </div>
                        {matDone[i] && rs.asset && (
                          <div style={{ padding: "8px 10px", marginBottom: 6, borderRadius: 6,
                            background: "rgba(178,106,0,.12)", fontSize: 12 }}>
                            재질이 반영된 새 USD가 이 스텝의 산출물로 교체됐습니다. <b>이후 스텝은 아직 예전 값으로 만들어진 상태</b>입니다.
                            <button style={{ fontSize: 12, marginLeft: 8 }} disabled={busy}
                              onClick={() => rerunAfter(i, rs.asset!)}>▶ 다음 스텝부터 다시 실행</button>
                          </div>
                        )}
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                          <thead><tr style={{ textAlign: "left", borderBottom: "2px solid var(--vsc-border)" }}>
                            <th style={{ padding: "4px 6px" }}>부품</th><th style={{ padding: "4px 6px" }}>재질</th><th style={{ padding: "4px 6px" }}>밀도</th><th style={{ padding: "4px 6px" }}>치수(mm)</th><th style={{ padding: "4px 6px" }}>부피법</th><th style={{ padding: "4px 6px" }}>질량(kg)</th><th style={{ padding: "4px 6px" }}>AI 선정 이유</th></tr></thead>
                          <tbody>{parts.map((p, k) => (
                            <tr key={k} style={{ borderBottom: "1px solid var(--vsc-border)" }}>
                              <td style={{ padding: "4px 6px", fontWeight: 600 }}>{p.name}</td>
                              <td style={{ padding: "4px 6px" }}>
                                <select value={matOv[i]?.[p.name] ?? p.material ?? ""} style={{ width: "auto", fontSize: 11.5, padding: "1px 4px" }}
                                  title="AI 추론 결과. 틀리면 바꾸고 위의 '재질 적용'을 누르세요."
                                  onChange={(e) => {
                                    const v = e.target.value;
                                    setMatOv((m) => {
                                      const cur = { ...(m[i] ?? {}) };
                                      if (v === (p.material ?? "")) delete cur[p.name]; else cur[p.name] = v;
                                      return { ...m, [i]: cur };
                                    });
                                  }}>
                                  {(matKeys.includes(p.material ?? "") ? matKeys : [...(p.material ? [p.material] : []), ...matKeys])
                                    .map((mk) => <option key={mk} value={mk}>{mk}</option>)}
                                </select>
                                {matOv[i]?.[p.name] && <span style={{ fontSize: 10, color: "#b26a00", marginLeft: 4 }}>수정</span>}
                                {p.bound_visual_material ? <span className="muted" style={{ fontSize: 10 }}> ←{p.bound_visual_material}</span> : null}
                              </td>
                              <td style={{ padding: "4px 6px" }}>{p.density ?? "—"}</td>
                              <td style={{ padding: "4px 6px" }} className="muted">{p.dims_mm ? p.dims_mm.map((d) => Math.round(d)).join("×") : "—"}</td>
                              <td style={{ padding: "4px 6px" }} className="muted">{pv[k]?.preview ? `shell(${pv[k].t}mm)` : (p.volume_method ?? "—")}</td>
                              <td style={{ padding: "4px 6px", fontWeight: 600 }}>{pv[k]?.preview ? <span title={`솔리드 ${p.solid_mass_kg} kg`} style={{ color: "var(--vsc-accent,#2e7d32)" }}>{r3(pv[k].mass)}</span> : r3(pv[k]?.mass ?? p.mass_kg)}</td>
                              <td style={{ padding: "4px 6px", maxWidth: 320 }} className="muted">
                                {p.material_reasoning ? <div>🎨 {p.material_reasoning}</div> : null}
                                {p.physics_reasoning ? <div>⚙ {p.physics_reasoning}</div> : null}
                                {p.shell_reason ? <div>🧱 {p.shell_reason}{p.auto_wall_mm ? ` (AI ${p.auto_wall_mm}mm)` : ""}</div> : null}
                                {!p.material_reasoning && !p.physics_reasoning && !p.shell_reason ? "—" : null}
                              </td>
                            </tr>))}</tbody>
                          <tfoot>
                            <tr style={{ borderTop: "2px solid var(--vsc-border)", fontWeight: 700 }}>
                              <td style={{ padding: "4px 6px" }} colSpan={5}>합계 ({parts.length}개 부품)</td>
                              <td style={{ padding: "4px 6px", color: "var(--vsc-accent,#2e7d32)" }}>{r3(dispTotal)} kg</td>
                              <td></td>
                            </tr>
                          </tfoot>
                        </table>
                      </div>
                      );
                    })()}
                  </div>
                )}
              </div>
            );
          })}

          {/* + 스텝 추가 — 정의 모드, 카테고리별 그룹 */}
          {mode === "define" && (
            <div style={{ textAlign: "center", marginTop: 10 }}>
              <button className="ghost" onClick={() => setShowAdd((v) => !v)}>+ 스텝 추가</button>
              {showAdd && (
                <div className="card-sub" style={{ maxWidth: 520, margin: "8px auto 0", textAlign: "left" }}>
                  <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
                    {tailTerminal ? "마지막 스텝(terminal) 뒤엔 못 붙입니다." : `현재 출력 「${typeLabel[tailType ?? ""] ?? tailType ?? "미정"}」 기준 — 호환 스텝만 활성:`}
                  </div>
                  {catOrder.map((cat) => (
                    <div key={cat} style={{ marginBottom: 4 }}>
                      <div className="muted" style={{ fontSize: 11, marginTop: 8, borderBottom: "1px solid var(--vsc-border)", paddingBottom: 2 }}>{cat}</div>
                      {addable.filter((c) => c.cat === cat).map((c) => {
                        const ok = !tailTerminal && compat(tailType, c.accepts);
                        return (
                          <div key={c.id} onClick={() => ok && addStep(c.id)}
                            style={{ padding: "5px 8px", borderRadius: 5, fontSize: 13, opacity: ok ? 1 : 0.4, cursor: ok ? "pointer" : "not-allowed", display: "flex", justifyContent: "space-between" }}>
                            <span>{ok ? "✅" : "🚫"} {c.icon} {c.name}</span>
                            <span className="muted" style={{ fontSize: 11 }}>{c.accepts.map((a) => typeLabel[a] ?? a).join("/")} → {typeLabel[c.produces] ?? c.produces}</span>
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 액션 */}
        <div className="row" style={{ marginTop: 14, gap: 8 }}>
          {mode === "run" ? (
            <>
              <button onClick={run} disabled={busy || !chain.length || (needsFile && !file)}>{busy ? "실행 중…" : "▶ 실행"}</button>
              {busy && <button className="ghost" onClick={() => acRef.current?.abort()}>취소</button>}
            </>
          ) : nameInput !== null ? (
            <div style={{ display: "flex", gap: 8, alignItems: "center", width: "100%", maxWidth: 520 }}>
              <input autoFocus value={nameInput} onChange={(e) => setNameInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") confirmSave(); if (e.key === "Escape") setNameInput(null); }}
                placeholder="파이프라인 이름" style={{ flex: 1 }} />
              <button onClick={confirmSave} disabled={!nameInput.trim()}>저장</button>
              <button className="ghost" onClick={() => setNameInput(null)}>취소</button>
            </div>
          ) : (
            <>
              <button onClick={startSave} disabled={!chain.length}>{editingId ? "저장(덮어쓰기)" : "저장"}</button>
              {chain.length > 0 && <button className="ghost" onClick={() => { setChain([]); setFormats([]); setResult(null); }}>비우기</button>}
            </>
          )}
        </div>
        {busy && prog?.total && (
          <p className="muted" style={{ marginTop: 8, fontSize: 13 }}>스텝 {prog.index}/{prog.total} — {prog.label} 실행 중…</p>
        )}
        {error && <p className="err" style={{ marginTop: 8 }}>{error}</p>}
        {result && !paused && !busy && (
          <p style={{ marginTop: 8, color: result.ok ? "#4ec9b0" : "#f48771" }}>
            {result.ok ? "✓ 파이프라인 완료 — 각 스텝 산출물을 위에서 받을 수 있습니다." : `✗ ${result.failed_at}번 스텝에서 실패 — 그 전까지 산출물은 받을 수 있습니다.`}
          </p>
        )}
        {paused && (() => {
          const Editor = EMBED_EDITORS[paused.cardId];
          const sp = spec(paused.cardId);
          if (!Editor) return <p className="err" style={{ marginTop: 8 }}>직접 편집기를 찾지 못했습니다: {paused.cardId}</p>;
          return (
            <div style={{ marginTop: 12, border: "1px solid #2d6cdf", borderRadius: 8, padding: 12 }}>
              <p className="muted" style={{ marginTop: 0 }}>⏸ <b>직접 모드</b> — 「{sp?.name ?? paused.cardId}」 단계에서 멈췄습니다. 아래에서 편집을 마치면 <b>자동으로 다음 단계로</b> 이어집니다.</p>
              <Editor
                manifest={{ id: paused.cardId, name: sp?.name ?? paused.cardId, description: "",
                  io: { input: { file: { accept: EMBED_ACCEPT[paused.cardId] ?? ".usd,.usda,.usdc,.usdz" } } } }}
                embedded={{ inputFile: paused.inputFile, onComplete: onManualComplete }} />
            </div>
          );
        })()}
      </div>
    </div>
  );
}
