import "./globals.css";

export const metadata = {
  title: "에이전트 플랫폼",
  description: "에이전트 플랫폼 — Sim-Ready 3D 워크플로우",
  openGraph: {
    title: "에이전트 플랫폼",
    description: "에이전트 플랫폼 — Sim-Ready 3D 워크플로우",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
