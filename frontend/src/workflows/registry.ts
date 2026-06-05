import type { ComponentType } from "react";
import AssetPrep from "./asset-prep/AssetPrep";
import SampleSum from "./sample-sum/SampleSum";
import MaterialUsd from "./material-usd/MaterialUsd";
import ContentMaterial from "./content-material/ContentMaterial";
import ContentPhysics from "./content-physics/ContentPhysics";
import ContentTexture from "./content-texture/ContentTexture";
import MaterialCompare from "./material-compare/MaterialCompare";
import MassPhysics from "./mass-physics/MassPhysics";
import SdTexture from "./sd-texture/SdTexture";
import TrinixModel from "./trinix-model/TrinixModel";
import TrinixSimready from "./trinix-simready/TrinixSimready";

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
  hidden?: boolean;
  requiresImageGen?: boolean;
  disabled?: boolean;
  disabledNote?: string;
  requiredPermissions?: string[];
  io?: { input?: Record<string, unknown>; output?: string[] };
};

// 셸이 마운트된 워크플로우 모듈에 주입하는 props(계약).
export type WorkflowModuleProps = {
  manifest: WorkflowManifest;
  onBack: () => void;
};

// "라우트 기반 모듈 + 매니페스트 컨벤션" — manifest.entry 키로 로컬 UI 모듈을 찾는다.
// 새 워크플로우 추가 = 백엔드에 폴더(manifest+handler) + 여기 한 줄.
export const MODULES: Record<string, ComponentType<WorkflowModuleProps>> = {
  "asset-prep": AssetPrep,
  "sample-sum": SampleSum,
  "material-usd": MaterialUsd,
  "content-material": ContentMaterial,
  "content-physics": ContentPhysics,
  "content-texture": ContentTexture,
  "material-compare": MaterialCompare,
  "mass-physics": MassPhysics,
  "sd-texture": SdTexture,
  "trinix-model": TrinixModel,
  "trinix-simready": TrinixSimready,
};
