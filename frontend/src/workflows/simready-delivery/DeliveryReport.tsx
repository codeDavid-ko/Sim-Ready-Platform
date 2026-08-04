"use client";

/** NVIDIA Sim-Ready Delivery 결과 UI(공용) — 카드와 파이프라인 스텝이 같은 화면을 쓴다.
 *
 * 표시 전용 섹션(검증 스텝·요구↔인풋·내용 점검·미충족 항목·패키지 결과)은 항상 렌더하고,
 * 액션(해결/빈 항목 채우기/USD·패키지 만들기)은 콜백을 준 곳에서만 버튼이 나온다.
 * → 파이프라인(세션 없음)은 콜백 없이 읽기 전용으로 쓴다. */

export type AssetRec = { id: string; filename: string; bytes: number; download_url: string };
export type FeatureRow = { feature: string; passed: boolean; precluded?: boolean; failing: string[] };
export type CodeRow = { code: string; meaning: string; fixable: boolean; fixclass: string; how?: string; optional?: boolean; features: string[] };
export type Content = { meshes: number; materials: number; rigid_bodies: number; colliders: number; grasp_curves: number; joints?: number; textures?: number };
export type Classify = { asset_class: string; gate_level: "ok" | "warn" | "block"; reason: string; joints: number; drives: number; articulation_roots: number; deformable: string[] };
export type Report = {
  passed: boolean; deliverable?: boolean; profile: string; version: string; ran: boolean;
  features: FeatureRow[]; failing_codes: string[]; codes: CodeRow[]; raw_stdout?: string;
  content?: Content; content_notes?: string[];
};
export type PkgInfo = {
  ok: boolean; log?: string; no_wrapp?: boolean; message?: string;
  hash_ok?: boolean | null; report_in_pkg?: boolean; evidence?: boolean;
  profile_used?: string; downgraded?: boolean;
};

// 검증 피처 → 한글 라벨(문서가 보여주는 검증 스텝). 리포트의 feature 키와 매핑.
export const FEATURE_LABEL: Record<string, string> = {
  FET000_CORE: "코어 — 네이밍·메타데이터 (SR.001/NP)",
  FET001_BASE_NEUTRAL: "지오메트리 — 단위·extent·노멀 (UN/VG)",
  FET003_BASE_NEUTRAL: "충돌 — 콜라이더 기본",
  FET003_BASE_PHYSX: "충돌 — PhysX sdf (COL.001)",
  FET004_BASE_NEUTRAL: "강체 — 멀티바디 기본",
  FET004_BASE_PHYSX: "강체 — PhysX 멀티바디 (RB.MB)",
  FET005_BASE_NEUTRAL: "그래스프 — 파지 벡터 (GSP.001)",
  FET006_BASE_MDL: "재질 — MDL 바인딩 (VM.*)",
  FET100_BASE_ISAACSIM: "Isaac — 페이로드 레이아웃 (ISA.001)",
};
export const featLabel = (f: string) => FEATURE_LABEL[f] ?? f;

/** 통과 표시됐지만 필수 내용이 0개 → 실제론 미충족(검증기가 미평가로 통과시킨 것). */
export function vacuousFeature(f: FeatureRow, content?: Content): boolean {
  const c = content;
  if (!f.passed || !c) return false;
  if (/FET004/.test(f.feature)) return c.rigid_bodies < 2 && (c.joints ?? 0) > 0;  // 멀티바디: 조인트 있을 때만 필수
  if (/FET003/.test(f.feature)) return c.rigid_bodies < 1;
  if (/FET005/.test(f.feature)) return c.grasp_curves === 0;
  if (/FET006/.test(f.feature)) return c.materials === 0;
  return false;
}

export function reportStatus(report: Report) {
  const optionalCodes = new Set((report.codes ?? []).filter((c) => c.optional).map((c) => c.code));
  const vacuous = (f: FeatureRow) => vacuousFeature(f, report.content);
  // 피처가 NVIDIA상 실패여도, 그 실패코드가 전부 optional(면제)면 '면제'로 본다.
  const featOptional = (f: FeatureRow) => !f.passed && f.failing.length > 0 && f.failing.every((c) => optionalCodes.has(c));
  const genuinePass = (f: FeatureRow) => f.passed && !f.precluded && !vacuous(f);
  const notPassing = report.features.filter((f) => !genuinePass(f) && !featOptional(f));
  return { vacuous, featOptional, genuinePass, notPassing };
}

type Props = {
  report: Report;
  classify?: Classify | null;
  /** 가동부(조인트)가 있어야 하는 에셋인지 — 요구↔인풋 표에서 조인트를 '필수'로 볼지 결정. */
  jointsExpected?: boolean;
  busy?: boolean;
  /** 아래 콜백을 준 곳에서만 해당 버튼이 나온다(없으면 읽기 전용). */
  onFix?: (codes: string[]) => void;
  onEnrich?: () => void;
  onDownloadUsd?: () => void;
  onPackage?: () => void;
  canPackage?: boolean;
  /** 패키지 버튼이 비활성일 때 옆에 띄울 설명. */
  packageBlockedNote?: string;
};

export default function DeliveryReport({
  report, classify, jointsExpected = false, busy = false,
  onFix, onEnrich, onDownloadUsd, onPackage, canPackage = false, packageBlockedNote,
}: Props) {
  const { vacuous, featOptional, notPassing } = reportStatus(report);
  const showOutputs = !!(onDownloadUsd || onPackage);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <label style={{ margin: 0 }}>검증 결과 — {report.profile} v{report.version}</label>
        {report.passed && notPassing.length === 0
          ? <span className="badge ok">✓ 납품 규격 통과 [PASSED]</span>
          : notPassing.length === 0 && report.deliverable
            ? <span className="badge" style={{ background: "#b26a00", color: "#fff" }}>○ 납품 가능 (optional 면제)</span>
            : <span className="badge" style={{ background: "#b00020", color: "#fff" }}>✗ 미충족 {notPassing.length}스텝 (검증코드 {report.failing_codes.length})</span>}
      </div>

      {/* §12 입력 게이트 — 에셋 종류 분류 */}
      {classify && (
        <div style={{ marginTop: 6, padding: "7px 10px", borderRadius: 6, fontSize: 12,
          background: classify.gate_level === "block" ? "rgba(176,0,32,.12)" : classify.gate_level === "warn" ? "rgba(178,106,0,.12)" : "var(--vsc-bg-subtle,#f3f6f4)" }}>
          <b>에셋 종류: {classify.asset_class}</b> {classify.gate_level === "block" ? "⛔ 납품 불가" : classify.gate_level === "warn" ? "⚠ 범위 밖" : ""}
          <span className="muted"> — {classify.reason}</span>
        </div>
      )}

      {/* 검증 스텝 결과 — 피처를 하나씩 점검한 결과(순서대로) */}
      <div style={{ marginTop: 8 }}>
        <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>검증 스텝 ({report.features.filter((f) => f.passed && !f.precluded && !vacuous(f)).length}/{report.features.length} 실제 통과) · NVIDIA simready-validate 판정</div>
        {report.features.map((f, i) => {
          const vac = vacuous(f);
          const opt = featOptional(f);
          const icon = f.precluded ? "⏸" : opt ? "○" : (!f.passed || vac) ? "✗" : "✓";
          const color = f.precluded ? "#b26a00" : opt ? "#b26a00" : (!f.passed || vac) ? "#b00020" : "var(--vsc-accent,#2e7d32)";
          return (
            <div key={f.feature} className="row" style={{ alignItems: "center", gap: 8, fontSize: 12.5, padding: "2px 0",
              animation: "fadeIn .3s ease both", animationDelay: `${i * 60}ms` }}>
              <span style={{ width: 18, textAlign: "center", color }}>{icon}</span>
              <span style={{ minWidth: 0 }}>{i + 1}. {featLabel(f.feature)}</span>
              {f.precluded && <span style={{ fontSize: 11, color: "#b26a00" }}>보류 — 상위 오류 먼저 해결해야 평가됨</span>}
              {opt && <span style={{ fontSize: 11, color: "#b26a00" }}>면제(optional) — NVIDIA가 optional 표기, 조인트 없어 건너뜀</span>}
              {!opt && !f.passed && !f.precluded && f.failing.length > 0 && (
                <span style={{ fontSize: 11, color: "#b00020", fontFamily: "monospace" }}>← {f.failing.join(", ")}</span>
              )}
              {f.passed && vac && <span style={{ fontSize: 11, color: "#b00020" }}>필수 내용 부족 — 미평가로 통과 표시됐을 뿐 실제 미충족</span>}
            </div>
          );
        })}
      </div>

      {/* 프로파일 요구 ↔ 실제 인풋 비교 표 */}
      {report.content && (() => {
        const c = report.content; const isIsaac = report.profile.includes("Isaac");
        // kind: required(필수·없으면✗) / advisory(권장·없어도○) / conditional(조건부·있을 때만 검증)
        type Row = { name: string; kind: "required" | "advisory" | "conditional"; need: string; have: number; ok: boolean };
        const rows: Row[] = [
          { name: "지오메트리(메시)", kind: "required", need: "≥1", have: c.meshes, ok: c.meshes >= 1 },
          { name: "강체 (RigidBody)", kind: "required", need: "≥1", have: c.rigid_bodies, ok: c.rigid_bodies >= 1 },
          (c.joints ?? 0) === 0
            ? { name: "멀티바디 (강체 수)", kind: "advisory" as const, need: "면제(조인트 없음·optional)", have: c.rigid_bodies, ok: c.rigid_bodies >= 2 }
            : { name: "멀티바디 (강체 수)", kind: "required" as const, need: "≥2 (FET004)", have: c.rigid_bodies, ok: c.rigid_bodies >= 2 },
          { name: "그래스프 벡터", kind: "required", need: "≥1", have: c.grasp_curves, ok: c.grasp_curves >= 1 },
          { name: "머티리얼 (MDL)", kind: isIsaac ? "advisory" : "required", need: isIsaac ? "불요(Isaac)" : "모든 메시", have: c.materials, ok: isIsaac ? true : c.materials >= 1 },
          { name: "충돌체 (Collision)", kind: "advisory", need: "권장(필수 아님)", have: c.colliders, ok: c.colliders >= 1 },
          jointsExpected
            ? { name: "조인트 (Joint)", kind: "required" as const, need: "≥1 (가동부 에셋)", have: c.joints ?? 0, ok: (c.joints ?? 0) >= 1 }
            : { name: "조인트 (Joint)", kind: "conditional" as const, need: "불필요(가동부 아님)", have: c.joints ?? 0, ok: true },
          { name: "텍스처 (Texture)", kind: "conditional", need: "있을 때만 검증(≤16384·컬러스페이스)", have: c.textures ?? 0, ok: true },
        ];
        const KIND = { required: { t: "필수", c: "#b00020" }, advisory: { t: "권장", c: "#b26a00" }, conditional: { t: "조건부", c: "#5a6b7a" } };
        return (
          <div style={{ marginTop: 12, overflowX: "auto" }}>
            <div className="muted" style={{ fontSize: 11, marginBottom: 2 }}>프로파일 요구 ↔ 실제 인풋 ({report.profile} v{report.version}) · 필수=없으면 ✗ / 권장=없어도 됨 / 조건부=있을 때만 검사</div>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
              <thead><tr style={{ textAlign: "left", borderBottom: "2px solid var(--vsc-border)" }}>
                <th style={{ padding: "4px 6px" }}>항목</th>
                <th style={{ padding: "4px 6px" }}>분류</th>
                <th style={{ padding: "4px 6px" }}>프로파일 요구</th>
                <th style={{ padding: "4px 6px" }}>실제 인풋</th>
                <th style={{ padding: "4px 6px" }}>상태</th>
              </tr></thead>
              <tbody>{rows.map((r) => {
                const has = r.have > 0;
                let color: string, label: string;
                if (r.kind === "conditional") { color = has ? "var(--vsc-accent,#2e7d32)" : "#5a6b7a"; label = has ? "✓ 있음(검증됨)" : "— 없음(무관)"; }
                else if (r.kind === "advisory") { color = r.ok ? "var(--vsc-accent,#2e7d32)" : "#b26a00"; label = r.ok ? "✓ 있음" : "○ 없음(권장)"; }
                else { color = r.ok ? "var(--vsc-accent,#2e7d32)" : "#b00020"; label = r.ok ? "✓ 있음" : "✗ 없음/부족"; }
                return (
                  <tr key={r.name} style={{ borderBottom: "1px solid var(--vsc-border)" }}>
                    <td style={{ padding: "4px 6px", fontWeight: 600 }}>{r.name}</td>
                    <td style={{ padding: "4px 6px", color: KIND[r.kind].c, fontSize: 11, fontWeight: 700 }}>{KIND[r.kind].t}</td>
                    <td style={{ padding: "4px 6px" }} className="muted">{r.need}</td>
                    <td style={{ padding: "4px 6px", fontWeight: 600 }}>{r.have}개</td>
                    <td style={{ padding: "4px 6px", color, fontWeight: 600 }}>{label}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        );
      })()}

      {/* 내용 점검 — 검증 통과 ≠ 내용 있음(공허한 통과 경고) */}
      {report.content && (
        <div style={{ marginTop: 10, padding: "8px 10px", borderRadius: 6,
          background: (report.content_notes?.length ?? 0) > 0 ? "rgba(178,106,0,.10)" : "var(--vsc-bg-subtle,#f3f6f4)" }}>
          <div style={{ fontSize: 12 }}>📦 <b>실제 내용</b> — 메시 {report.content.meshes} · 머티리얼 {report.content.materials} · 강체 {report.content.rigid_bodies} · 충돌체 {report.content.colliders} · 그래스프 {report.content.grasp_curves}</div>
          {(report.content_notes?.length ?? 0) > 0 && (
            <div style={{ marginTop: 6 }}>
              <div style={{ fontSize: 12, color: "#b26a00", fontWeight: 600 }}>⚠ 검증은 통과해도 아래 내용이 비어 있습니다 — 검증기는 “있는 프림의 위반”만 보므로, 없으면 통과로 표시됩니다.</div>
              <ul style={{ margin: "4px 0 6px", paddingLeft: 18, fontSize: 12 }}>
                {report.content_notes!.map((n, i) => <li key={i} style={{ color: "#8a5a00" }}>{n}</li>)}
              </ul>
              {(() => {
                const c = report.content!;
                // 자동으로 채울 수 있는 것만(강체는 메시가 ≥2개 있어야 추가 가능 — 1개면 멀티바디 자동 불가).
                const add: string[] = [];
                if (c.colliders === 0) add.push("충돌체(sdf)");
                if (c.rigid_bodies < 2 && c.meshes >= 2) add.push(c.rigid_bodies === 0 ? "강체+질량" : "강체(2번째)");
                if (c.grasp_curves === 0) add.push("그래스프");
                if (c.materials === 0) add.push("머티리얼");
                if (add.length === 0) {
                  return <p className="muted" style={{ fontSize: 11, margin: 0 }}>자동으로 채울 수 있는 빈 항목이 없습니다 — 멀티바디(강체 ≥2)는 메시가 1개라 부품을 나누거나 추가해야 합니다(아래 “직접 손봐야 함” 참고).</p>;
                }
                if (!onEnrich) {
                  return <p className="muted" style={{ fontSize: 11, margin: 0 }}>자동으로 채울 수 있는 빈 항목: <b>{add.join(", ")}</b> — 납품 카드에서 “빈 항목 채우기”로 추가할 수 있습니다.</p>;
                }
                const massNote = add.some((a) => a.startsWith("강체")) ? " 질량은 1kg 기본(정확한 값은 물성 카드 권장)." : "";
                return (<>
                  <button onClick={onEnrich} disabled={busy} style={{ fontSize: 12 }}>⚙ 빈 항목 채우기 ({add.join(", ")})</button>
                  <span className="muted" style={{ fontSize: 11, marginLeft: 8 }}>이 파일에 빠진 <b>{add.join(", ")}</b>만 추가합니다(이미 있는 건 그대로).{massNote}</span>
                </>);
              })()}
            </div>
          )}
        </div>
      )}

      {/* 미충족 항목 — 자동 해결 / 수동 / 면제(optional)로 분리 */}
      {!report.passed && report.codes.length > 0 && (() => {
        const auto = report.codes.filter((c) => c.fixable && !c.optional);
        const manual = report.codes.filter((c) => !c.fixable && !c.optional);
        const optional = report.codes.filter((c) => c.optional);
        const codeRow = (c: CodeRow) => (
          <tr key={c.code} style={{ borderBottom: "1px solid var(--vsc-border)" }}>
            <td style={{ padding: "4px 6px", fontWeight: 600, fontFamily: "monospace" }}>{c.code}</td>
            <td style={{ padding: "4px 6px" }}><span style={{ fontSize: 10, fontWeight: 700, padding: "1px 6px", borderRadius: 8,
              background: c.fixclass === "AUTO" ? "rgba(27,94,32,.16)" : c.fixclass === "INPUT" ? "rgba(138,82,0,.16)" : "rgba(90,90,90,.16)",
              color: c.fixclass === "AUTO" ? "#1b5e20" : c.fixclass === "INPUT" ? "#8a5200" : "#555" }}>{c.fixclass}</span></td>
            <td style={{ padding: "4px 6px" }}>
              {c.meaning}
              {c.how && <div style={{ fontSize: 11, marginTop: 1, color: c.fixable ? "#1b5e20" : "#9a0010" }}><b>{c.fixable ? "→ 이렇게 해결: " : "→ "}</b>{c.how}</div>}
            </td>
            <td style={{ padding: "4px 6px" }} className="muted">{c.features.join(", ")}</td>
            {onFix && c.fixable && <td style={{ padding: "4px 6px" }}><button className="ghost" style={{ padding: "2px 8px", fontSize: 12 }} disabled={busy} onClick={() => onFix([c.code])}>해결</button></td>}
          </tr>
        );
        return (
          <div style={{ marginTop: 12 }}>
            <b style={{ fontSize: 13 }}>납품 규격 미충족 항목</b>
            {/* ✅ 프로그램이 자동 해결 가능 */}
            {auto.length > 0 && (
              <div style={{ marginTop: 8, border: "1px solid rgba(46,125,50,.35)", borderRadius: 6, padding: "8px 10px" }}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontWeight: 700, color: "#1b5e20" }}>✅ 프로그램이 자동으로 채워줄 수 있는 항목 ({auto.length})</span>
                  {onFix && <button disabled={busy} onClick={() => onFix(auto.map((c) => c.code))}>🛠 전체 해결</button>}
                </div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, marginTop: 6 }}>
                    <tbody>{auto.map(codeRow)}</tbody>
                  </table>
                </div>
              </div>
            )}
            {/* ✋ 프로그램이 자동으로 못 하는 항목 */}
            {manual.length > 0 && (
              <div style={{ marginTop: 8, border: "1px solid rgba(176,0,32,.30)", borderRadius: 6, padding: "8px 10px" }}>
                <span style={{ fontWeight: 600, color: "#b00020" }}>✋ 프로그램이 자동으로 못 하는 항목 ({manual.length}) — 직접 손봐야 함</span>
                <div className="muted" style={{ fontSize: 11, margin: "2px 0 4px" }}>형상/레이아웃 재구성(STRUCT)이라 자동수정 불가. 원본 형상을 고치거나 다른 프로파일/버전을 쓰세요.</div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                    <tbody>{manual.map(codeRow)}</tbody>
                  </table>
                </div>
              </div>
            )}
            {/* ○ NVIDIA optional 표기 → 면제 처리(패키지는 evidence 모드) */}
            {optional.length > 0 && (
              <div style={{ marginTop: 8, border: "1px solid rgba(178,106,0,.35)", borderRadius: 6, padding: "8px 10px" }}>
                <span style={{ fontWeight: 600, color: "#b26a00" }}>○ 면제(optional) 항목 ({optional.length}) — 납품 막지 않음</span>
                <div className="muted" style={{ fontSize: 11, margin: "2px 0 4px" }}>NVIDIA가 profiles.toml에서 optional로 표기한 항목. 패키지는 evidence 모드(약한 conformance)로 빌드됩니다.</div>
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
                    <tbody>{optional.map(codeRow)}</tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        );
      })()}

      {/* 산출물(다운로드) — 카드에서만. 파이프라인은 스텝 행의 zip 버튼을 쓴다. */}
      {showOutputs && (
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid var(--vsc-border)" }}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {onDownloadUsd && <button className="ghost" onClick={onDownloadUsd} disabled={busy}>📄 USD 다운로드 (의존자산 포함)</button>}
            {onPackage && (canPackage
              ? <button onClick={onPackage} disabled={busy}>📦 SimReady 패키지 만들기</button>
              : <span className="muted" style={{ fontSize: 12 }}>· {packageBlockedNote ?? "모든 항목을 실제로 통과해야(필수 내용 포함) 패키지가 활성화됩니다"}</span>)}
          </div>
          <p className="muted" style={{ fontSize: 11, marginTop: 6 }}>
            USD = 검증·수정된 USD + 머티리얼/텍스처/썸네일 묶음(어디서나 열림). 패키지 = WRAPP(BOM·해시·conformance) .zip.
            <b> 이 프로그램은 NVIDIA에 직접 제출하지 않습니다</b> — 위 두 산출물을 받아 직접 납품/공유하세요.
          </p>
        </div>
      )}
    </>
  );
}

/** 패키지 결과(동봉물·해시봉인·evidence 경고). onDownload 를 주면 다운로드 버튼도 렌더. */
export function PackageResult({ pkg, filename, bytes, onDownload, showLog = true }: {
  pkg: PkgInfo; filename?: string; bytes?: number; onDownload?: () => void; showLog?: boolean;
}) {
  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <label style={{ margin: 0 }}>패키지 {pkg.ok ? "완료" : "실패"}</label>
        {pkg.no_wrapp && <span className="badge">--no-wrapp (BOM 없음)</span>}
      </div>
      {pkg.message && <p className="err">{pkg.message}</p>}
      {pkg.downgraded && (
        <p style={{ fontSize: 12, color: "#b26a00", margin: "2px 0" }}>
          ⚠ 요청한 프로파일로 통과하지 못해 <b>{pkg.profile_used}</b> 로 패키징됐습니다 — 자산 프로파일 conformance가 아닌 <b>패키징 전용 conformance</b>입니다. NVIDIA 정식 제출 전 확인 필요.
        </p>
      )}
      {pkg.evidence && (
        <p style={{ fontSize: 12, color: "#b26a00", margin: "2px 0" }}>⚠ evidence 모드로 빌드됨 — 단일바디(FET004 면제)라 사전검증 우회. <b>표준 [PASSED] 도장이 아닌 약한(evidence-form) conformance</b>입니다. NVIDIA 정식 제출 시 단일바디 허용 여부 확인 필요.</p>
      )}
      {pkg.ok && (
        <ul style={{ margin: "4px 0", paddingLeft: 18, fontSize: 12 }}>
          <li>✓ 검증 리포트 동봉(validation_report.json) {pkg.report_in_pkg ? "" : "— (확인 안 됨)"}</li>
          <li>✓ 프리뷰(썸네일) + USD + 머티리얼 동봉</li>
          <li>{pkg.hash_ok ? "✓" : "—"} content_hash == conformance {pkg.hash_ok ? "(이식성·봉인 일치)" : "(미확인)"}</li>
          {!pkg.no_wrapp && <li>✓ WRAPP: BOM·해시·conformance</li>}
        </ul>
      )}
      {onDownload && (
        <div className="row" style={{ marginTop: 8, gap: 8, alignItems: "center" }}>
          <button onClick={onDownload}>📥 패키지 .zip 다운로드</button>
          {filename && <span className="muted" style={{ fontSize: 11 }}>{filename}{bytes ? ` · ${(bytes / 1024).toFixed(0)} KB` : ""}</span>}
        </div>
      )}
      {showLog && pkg.log && <pre style={{ marginTop: 8, maxHeight: 220, overflow: "auto", fontSize: 11 }}>{pkg.log}</pre>}
    </>
  );
}
