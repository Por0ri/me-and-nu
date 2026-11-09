import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The local preview is also opened by IPv4 loopback; allow its dev HMR origin.
  allowedDevOrigins: ["127.0.0.1"],
  // Git ignore alone does not prevent Next from copying fs-read sources into
  // server deployment traces when a developer builds with local files present.
  outputFileTracingExcludes: { "*": ["./dev/policy-review/**/*"] },
  async redirects() {
    return [
      {
        source: "/",
        has: [{ type: "host", value: "checking\\.me-nu\\.kr" }],
        destination: "/pivot",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
