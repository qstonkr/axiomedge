import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output ships a self-contained server bundle in
  // .next/standalone — used by the Docker image (Day 10).
  output: "standalone",
};

export default nextConfig;
