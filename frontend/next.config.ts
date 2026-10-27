import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The local preview is also opened by IPv4 loopback; allow its dev HMR origin.
  allowedDevOrigins: ["127.0.0.1"],
};

export default nextConfig;
