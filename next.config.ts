import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  agentRules: false,
  // The UI is built once into static files and served by the Python backend.
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
};

export default nextConfig;
