"use client";

import { useEffect, useRef, useState } from "react";
import { blobUrl, submitAndPoll } from "@/lib/api";

/**
 * SpinViewer — 옴니버스(Isaac Sim RTX)로 360° 프레임을 렌더해, 마우스 드래그로
 * 객체를 회전시키는 'object-movie' 뷰어. <model-viewer> GLB 가 PBR 근사인 반면,
 * 이건 실제 MDL/MaterialX/vMaterials 룩 그대로 + 마우스 인터랙션.
 *
 * 어떤 카드든 등록된 USD/USDZ 에셋 id 만 주면 공용 /api/workflows/spin-submit 사용.
 */
export default function SpinViewer({ assetId, label }: { assetId: string; label?: string }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [frames, setFrames] = useState<string[]>([]);
  const [idx, setIdx] = useState(0);
  const refs = useRef<string[]>([]);
  const drag = useRef<{ x: number; idx: number } | null>(null);

  useEffect(() => () => { refs.current.forEach((u) => URL.revokeObjectURL(u)); }, []);

  async function gen() {
    setErr(null);
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("asset_id", assetId);
      fd.append("frames", "36");
      fd.append("res", "540");
      const r = await submitAndPoll<{ frames: string[]; count: number }>(
        `/api/workflows/spin-submit`, fd,
      );
      const urls: string[] = [];
      for (const u of r.frames) {
        try {
          const b = await blobUrl(u);
          refs.current.push(b);
          urls.push(b);
        } catch { /* skip frame */ }
      }
      if (!urls.length) throw new Error("프레임을 가져오지 못했습니다.");
      setFrames(urls);
      setIdx(0);
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  }

  function onDown(e: React.PointerEvent) {
    drag.current = { x: e.clientX, idx };
    try { (e.currentTarget as Element).setPointerCapture(e.pointerId); } catch { /* noop */ }
  }
  function onMove(e: React.PointerEvent) {
    if (!drag.current || !frames.length) return;
    const dx = e.clientX - drag.current.x;
    const step = Math.round(dx / 7); // 7px 당 한 프레임
    let n = (drag.current.idx - step) % frames.length;
    if (n < 0) n += frames.length;
    setIdx(n);
  }
  function onUp() { drag.current = null; }

  return (
    <div style={{ marginTop: 8 }}>
      <button className="ghost" onClick={gen} disabled={busy}>
        {busy ? "RTX 프레임 렌더 중… (수 분)" : (label ?? "🖱 인터랙티브 RTX 뷰어")}
      </button>
      {err && <p className="err">{err}</p>}
      {frames.length > 0 && (
        <div
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerLeave={onUp}
          style={{ touchAction: "none", cursor: "grab", userSelect: "none", marginTop: 8 }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={frames[idx]}
            alt={`frame ${idx + 1}`}
            draggable={false}
            style={{ width: "100%", borderRadius: 8, background: "#0d1117", display: "block" }}
          />
          <p className="muted" style={{ marginTop: 4 }}>
            드래그해서 360° 회전 · {idx + 1}/{frames.length} 프레임 · 실제 RTX 재질(Omniverse)
          </p>
        </div>
      )}
    </div>
  );
}
