"use client";

// 파이프라인은 이제 대시보드 셸 안의 섹션(사이드바 유지)으로 통합됨.
// 이 옛 별도 라우트로 들어오면 홈으로 보낸다(거기서 사이드바 '파이프라인' 사용).
import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function PipelinesRedirect() {
  const router = useRouter();
  useEffect(() => { router.replace("/"); }, [router]);
  return null;
}
