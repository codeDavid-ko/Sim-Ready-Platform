"use client";

import {
  Bone,
  Package,
  Palette,
  Scale,
  Brush,
  Columns2,
  Workflow,
  Calculator,
  Sparkles,
  Box,
  Factory,
  Video,
  Layers,
  type LucideIcon,
} from "lucide-react";

// 워크플로우 id → Lucide 라인 아이콘. (이모지 대신 한 세트로 통일된 단색 선 아이콘)
// 새 카드 추가 시 여기 한 줄. 매핑이 없으면 Layers 로 폴백.
export const WF_ICONS: Record<string, LucideIcon> = {
  "articulation": Bone,            // 관절 추가
  "asset-prep": Package,           // 형상 → USD 변환
  "material-usd": Palette,         // 재질 추론(NdotLight)
  "content-material": Palette,     // 재질 추론(NVIDIA)
  "mass-physics": Scale,           // 질량·접촉물리(NdotLight)
  "content-physics": Scale,        // 물리 추론(NVIDIA)
  "content-texture": Brush,        // 텍스처 추론(NVIDIA)
  "sd-texture": Sparkles,          // 텍스처 생성(로컬 SD)
  "material-compare": Columns2,    // 엔진 비교: 재질
  "physics-compare": Columns2,     // 엔진 비교: 물리
  "trinix-model": Box,             // 3D 모델링(Trinix)
  "trinix-simready": Factory,      // 3D → 재질·물성 파이프라인
  "turntable": Video,              // 턴테이블 영상 렌더
  "pipelines": Workflow,           // 자동화 파이프라인
  "sample-sum": Calculator,        // 샘플
};

// 카드/헤더에서 쓰는 아이콘. id 로 Lucide 컴포넌트를 찾아 currentColor 로 그린다.
export function WfIcon({
  id,
  size = 24,
  strokeWidth = 1.9,
  className,
}: {
  id?: string;
  size?: number;
  strokeWidth?: number;
  className?: string;
}) {
  const Ico = (id && WF_ICONS[id]) || Layers;
  return <Ico size={size} strokeWidth={strokeWidth} className={className} aria-hidden />;
}
