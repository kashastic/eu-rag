/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone", // self-contained server bundle for a small Docker image
  // API base for the FastAPI backend. Same-origin in production behind a
  // reverse proxy; override for split deploys.
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "",
  },
  // lint/type-check run in CI, not as a release-build gate
  eslint: { ignoreDuringBuilds: true },
};

export default nextConfig;
