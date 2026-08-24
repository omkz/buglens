import type { NextConfig } from "next";

const isDevelopment = process.env.NODE_ENV === "development";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    if (!isDevelopment) {
      return [];
    }

    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
