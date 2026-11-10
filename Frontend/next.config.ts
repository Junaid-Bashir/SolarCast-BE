import type { NextConfig } from "next";
const nextConfig: NextConfig = {
  // Keep production builds separate from the local dev server's generated files.
  distDir: process.env.SOLARCAST_DIST_DIR || (process.env.NODE_ENV === "production" ? ".next-production" : ".next"),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/:path*` }];
  },
};
export default nextConfig;
