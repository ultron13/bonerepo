/** @type {import('next').NextConfig} */
const nextConfig = {
  // The browser reaches the API directly; the server component tree never
  // proxies it, so there is one place a token lives and one origin to reason
  // about in the target policy.
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
