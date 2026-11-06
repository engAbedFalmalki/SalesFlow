import type { NextConfig } from "next";

// Public (single-origin) build, used by scripts/public-link.ps1: the browser calls /api on the
// site's own address and Next.js forwards it to the API. Off unless SALESFLOW_API_PROXY is set.
const apiProxy = process.env.SALESFLOW_API_PROXY?.replace(/\/$/, "");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  distDir: process.env.NEXT_DIST_DIR || ".next",
  ...(apiProxy && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${apiProxy}/api/:path*` }];
    },
  }),
};

export default nextConfig;
