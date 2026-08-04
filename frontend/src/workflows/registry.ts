import type { ComponentType } from "react";
import AssetPrep from "./asset-prep/AssetPrep";
import Articulation from "./articulation/Articulation";
import SampleSum from "./sample-sum/SampleSum";
import MaterialUsd from "./material-usd/MaterialUsd";
import ContentMaterial from "./content-material/ContentMaterial";
import ContentPhysics from "./content-physics/ContentPhysics";
import ContentTexture from "./content-texture/ContentTexture";
import MaterialCompare from "./material-compare/MaterialCompare";
import PhysicsCompare from "./physics-compare/PhysicsCompare";
import MassPhysics from "./mass-physics/MassPhysics";
import SdTexture from "./sd-texture/SdTexture";
import TrinixModel from "./trinix-model/TrinixModel";
import TrinixSimready from "./trinix-simready/TrinixSimready";
import Turntable from "./turntable/Turntable";
import Pipelines from "./pipelines/Pipelines";
import SimReadyDelivery from "./simready-delivery/SimReadyDelivery";
import LookMerge from "./look-merge/LookMerge";
import Grasp from "./grasp/Grasp";

// 백엔드 manifest.json 과 같은 모양 (셸은 이것만 알고 내부는 모른다).
export type WorkflowManifest = {
  id: string;
  name: string;
  description: string;
  tagline?: string;
  icon?: string;
  version: string;
  entry: string;
  category?: string;
  order?: number;
  hidden?: boolean;
  dev?: boolean;
  requiresImageGen?: boolean;
  disabled?: boolean;
  disabledNote?: string;
  requiredPermissions?: string[];
  io?: { input?: Record<string, unknown>; output?: string[] };
};

// 프론트 전용 "개발중" 오버라이드 — 백엔드 매니페스트를 안 건드리고(재시작 없이) UI에서만
// 특정 카드를 Labs(개발중)로 취급한다. (예: NVIDIA 물리추론 = content-physics)
export const DEV_OVERRIDE = new Set<string>(["content-physics"]);
export function isDevCard(m: { id: string; dev?: boolean }): boolean {
  return !!m.dev || DEV_OVERRIDE.has(m.id);
}

// 셸이 마운트된 워크플로우 모듈에 주입하는 props(계약).
export type WorkflowModuleProps = {
  manifest: WorkflowManifest;
  onBack: () => void;
};

// "라우트 기반 모듈 + 매니페스트 컨벤션" — manifest.entry 키로 로컬 UI 모듈을 찾는다.
// 새 워크플로우 추가 = 백엔드에 폴더(manifest+handler) + 여기 한 줄.
export const MODULES: Record<string, ComponentType<WorkflowModuleProps>> = {
  "asset-prep": AssetPrep,
  "articulation": Articulation,
  "sample-sum": SampleSum,
  "material-usd": MaterialUsd,
  "content-material": ContentMaterial,
  "content-physics": ContentPhysics,
  "content-texture": ContentTexture,
  "material-compare": MaterialCompare,
  "physics-compare": PhysicsCompare,
  "mass-physics": MassPhysics,
  "sd-texture": SdTexture,
  "trinix-model": TrinixModel,
  "trinix-simready": TrinixSimready,
  "turntable": Turntable,
  "pipelines": Pipelines,
  "nvidia-simready-delivery": SimReadyDelivery,
  "look-merge": LookMerge,
  "grasp": Grasp,
};
