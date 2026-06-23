#!/usr/bin/env node
import net from "node:net";
import { execSync } from "node:child_process";

const portArg = Number(process.argv[2] || process.env.DEV_PORT || 1420);
const host = "127.0.0.1";

function isPortInUse(port) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(800);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.once("error", () => resolve(false));
    socket.connect(port, host);
  });
}

function getPidsOnPortWindows(port) {
  const out = execSync(`netstat -ano -p tcp | findstr :${port}`, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  const pids = new Set();
  for (const line of out.split(/\r?\n/)) {
    const cols = line.trim().split(/\s+/);
    if (cols.length < 5) continue;
    const local = cols[1] || "";
    const state = cols[3] || "";
    const pid = cols[4] || "";
    if (!local.endsWith(`:${port}`)) continue;
    if (state.toUpperCase() !== "LISTENING") continue;
    if (/^\d+$/.test(pid)) pids.add(pid);
  }
  return [...pids];
}

function getPidsOnPortUnix(port) {
  const out = execSync(`lsof -ti tcp:${port}`, {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  return out
    .split(/\r?\n/)
    .map((v) => v.trim())
    .filter((v) => /^\d+$/.test(v));
}

function killPids(pids) {
  if (!pids.length) return;
  if (process.platform === "win32") {
    for (const pid of pids) {
      execSync(`taskkill /PID ${pid} /F`, { stdio: "ignore" });
    }
    return;
  }
  execSync(`kill -9 ${pids.join(" ")}`, { stdio: "ignore" });
}

async function main() {
  if (!Number.isFinite(portArg) || portArg <= 0) {
    throw new Error(`유효하지 않은 포트: ${String(portArg)}`);
  }

  const inUse = await isPortInUse(portArg);
  if (!inUse) {
    console.log(`[dev-port] ${portArg} 포트 사용 가능`);
    return;
  }

  console.log(`[dev-port] ${portArg} 포트 점유 감지, 기존 프로세스 정리 시도`);
  let pids = [];
  try {
    pids =
      process.platform === "win32"
        ? getPidsOnPortWindows(portArg)
        : getPidsOnPortUnix(portArg);
  } catch {
    pids = [];
  }

  if (!pids.length) {
    throw new Error(`[dev-port] ${portArg} 포트 사용 중이지만 PID를 찾지 못했습니다.`);
  }

  killPids(pids);
  await new Promise((r) => setTimeout(r, 600));
  const stillInUse = await isPortInUse(portArg);
  if (stillInUse) {
    throw new Error(`[dev-port] ${portArg} 포트 정리 실패`);
  }
  console.log(`[dev-port] ${portArg} 포트 정리 완료`);
}

main().catch((err) => {
  console.error(String(err?.message || err));
  process.exit(1);
});
