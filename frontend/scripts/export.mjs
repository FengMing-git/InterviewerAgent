/**
 * next build 之后把静态导出产物 out/ 拷到 backend/static/（Plan §8 发布形态），
 * 由 FastAPI 托管并做 SPA fallback。等价于手动：
 *   rm -rf ../backend/static && cp -r out ../backend/static
 * 挂在 package.json 的 postbuild，npm run build 时自动执行。
 */

import { cp, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.resolve(scriptDir, "../out");
const staticDir = path.resolve(scriptDir, "../../backend/static");

if (!existsSync(outDir)) {
  console.error("[export] 未找到 frontend/out，请先执行 next build");
  process.exit(1);
}

await rm(staticDir, { recursive: true, force: true });
await cp(outDir, staticDir, { recursive: true });
console.log(`[export] 已拷贝 ${outDir} → ${staticDir}`);
