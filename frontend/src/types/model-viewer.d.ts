import type { DetailedHTMLProps, HTMLAttributes } from "react";

// <model-viewer> 웹 컴포넌트를 JSX 에서 쓰기 위한 최소 타입 선언.
declare global {
  namespace JSX {
    interface IntrinsicElements {
      "model-viewer": DetailedHTMLProps<
        HTMLAttributes<HTMLElement> & {
          src?: string;
          alt?: string;
          "camera-controls"?: boolean;
          "auto-rotate"?: boolean;
          "shadow-intensity"?: string | number;
          exposure?: string | number;
        },
        HTMLElement
      >;
    }
  }
}

export {};
