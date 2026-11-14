import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 静态导出（Plan §8）：产物 out/ 拷到 backend/static/ 由 FastAPI 托管。
  // 静态导出没有服务端路由 → 页面参数一律用查询字符串（?id=）。
  output: "export",
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
