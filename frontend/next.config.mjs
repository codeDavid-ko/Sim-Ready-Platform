/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // same-origin 프록시: 브라우저의 /api/* 를 백엔드로 넘긴다.
  // 덕분에 터널은 프런트 포트 하나만 열면 되고 CORS·별도 API URL 이 필요 없다.
  async rewrites() {
    const backend = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};
export default nextConfig;
