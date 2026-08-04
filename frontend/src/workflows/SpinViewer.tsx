"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, submitAndPoll } from "@/lib/api";

/**
 * SpinViewer — 옴니버스(Isaac Sim RTX)로 고도×방위각 격자 프레임을 렌더해, 마우스로
 * 객체를 좌우 회전 + 상하 고도 + 휠 확대 하는 'object-movie' 뷰어.
 * <model-viewer> GLB 가 PBR 근사인 반면, 이건 실제 MDL/MaterialX/vMaterials 룩 그대로.
 *
 * 어떤 카드든 등록된 USD/USDZ 에셋 id 만 주면 공용 /api/workflows/spin-submit 사용.
 * 응답: { frames[], rows(고도), cols(방위각) } — frames[row*cols + col].
 */
const AZ = 24;      // 방위각(좌우) 프레임 수
const ELEV = 3;     // 고도(상하) 단계 수

export default function SpinViewer({ assetId, label }: { assetId: string; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [frames, setFrames] = useState<string[]>([]);
  const [grid, setGrid] = useState<{ rows: number; cols: number }>({ rows: 1, cols: 1 });
  const [col, setCol] = useState(0);   // 방위각
  const [row, setRow] = useState(0);   // 고도
  const [zoom, setZoom] = useState(1); // 1~4
  const refs = useRef<string[]>([]);
  const drag = useRef<{ x: number; y: number; col: number; row: number } | null>(null);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); }, []);

  // 휠 줌: React onWheel 은 passive 라 preventDefault 가 안 먹어 페이지가 같이 스크롤된다.
  // 컨테이너에 non-passive 네이티브 리스너를 붙여 페이지 스크롤을 막고 줌만 적용.
  useEffect(() => {
    const el = boxRef.current;
    if (!el || !frames.length) return;
    const onWheelNative = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => Math.max(1, Math.min(4, +(z - e.deltaY * 0.0015).toFixed(2))));
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
  }, [frames.length]);

  async function gen() {
    setErr(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("asset_id", assetId);
      fd.append("frames", String(AZ));
      fd.append("res", "640");
      fd.append("elevations", String(ELEV));
      const r = await submitAndPoll<{ frames: string[]; count: number; rows?: number; cols?: number }>(
        `/api/workflows/spin-submit`, fd,
      );
      const urls: string[] = [];
      for (const u of r.frames) {
        try { const b = await blobUrl(u); refs.current.push(b); urls.push(b); } catch { /* skip */ }
      }
      if (!urls.length) throw new Error("프레임을 가져오지 못했습니다.");
      const cols = r.cols && r.cols > 0 ? r.cols : urls.length;
      const rows = r.rows && r.rows > 0 ? r.rows : 1;
      setFrames(urls);
      setGrid({ rows, cols });
      setCol(0);
      setRow(Math.floor(rows / 2));  // 중간 고도에서 시작
      setZoom(1);
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  function onDown(e: React.PointerEvent) {
    drag.current = { x: e.clientX, y: e.clientY, col, row };
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* noop */ }
  }
  function onMove(e: React.PointerEvent) {
    if (!drag.current || !frames.length) return;
    const { cols, rows } = grid;
    const dx = e.clientX - drag.current.x;
    const dy = e.clientY - drag.current.y;
    // 좌우: 방위각(순환). 상하: 고도(위로 끌면 위에서 보기 = row 증가, 클램프).
    let c = (drag.current.col - Math.round(dx / 9)) % cols;
    if (c < 0) c += cols;
    let rw = drag.current.row + Math.round(dy / 28);
    rw = Math.max(0, Math.min(rows - 1, rw));
    setCol(c);
    setRow(rw);
  }
  function onUp() { drag.current = null; }

  const idx = Math.min(row * grid.cols + col, frames.length - 1);

  return (
    <div style={{ marginTop: 8 }}>
      <button className="ghost" onClick={gen} disabled={busy}>
        {busy ? "RTX 프레임 렌더 중… (수십 초~수 분)" : (label ?? "🖱 인터랙티브 RTX 뷰어")}
      </button>
      {err && <p className="err">{err}</p>}
      {frames.length > 0 && (
        <>
          <div
            ref={boxRef}
            onPointerDown={onDown}
            onPointerMove={onMove}
            onPointerUp={onUp}
            onPointerLeave={onUp}
            style={{
              touchAction: "none", cursor: "grab", userSelect: "none", marginTop: 8,
              overflow: "hidden", borderRadius: 8, background: "#0d1117",
              overscrollBehavior: "contain",
            }}
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={frames[idx]}
              alt={`frame ${idx + 1}`}
              draggable={false}
              style={{
                width: "100%", display: "block",
                transform: `scale(${zoom})`, transformOrigin: "center center",
                transition: drag.current ? "none" : "transform 0.08s",
              }}
            />
          </div>
          <div className="row" style={{ justifyContent: "space-between", marginTop: 4 }}>
            <span className="muted" style={{ fontSize: 12 }}>
              좌우 드래그=회전 · 상하 드래그=고도 · 휠=확대 · 실제 RTX 재질
            </span>
            <span className="muted" style={{ fontSize: 11 }}>
              방위 {col + 1}/{grid.cols} · 고도 {row + 1}/{grid.rows} · {zoom.toFixed(1)}×
              {zoom > 1 && <button className="ghost" style={{ padding: "0 6px", marginLeft: 6, fontSize: 11 }} onClick={() => setZoom(1)}>리셋</button>}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
