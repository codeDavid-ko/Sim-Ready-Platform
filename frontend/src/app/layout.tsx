import "./globals.css";

export const metadata = {
  title: "Sim-Ready",
  description: "Sim-Ready — NVIDIA Isaac/Omniverse용 3D Sim-Ready 워크플로우 플랫폼",
  openGraph: {
    title: "Sim-Ready",
    description: "Sim-Ready — NVIDIA Isaac/Omniverse용 3D Sim-Ready 워크플로우 플랫폼",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
