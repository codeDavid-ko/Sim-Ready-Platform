"use client";

/** 파라미터 옆 작은 ⓘ — 마우스 올리면(또는 포커스) 설명 말풍선. 모든 카드 공용. */
export default function Tip({ t }: { t: string }) {
  return (
    <span className="tip" tabIndex={0} role="img" aria-label={t}>
      <span aria-hidden="true">i</span><span className="tip-bub">{t}</span>
    </span>
  );
}
