// 岗位状态：存在本地，一处改动全局同步（Chat 页推荐位、技能库默认筛选、侧栏标识都要跟着变）。
// 不上后端：岗位是"我怎么用这个台子"的偏好，不是组织架构里的职位，不需要跨设备强一致。
import { useCallback, useEffect, useState } from "react";

const KEY = "wb.role.v1";
const EVT = "wb-role-change";

export function readRole(): string {
  try {
    return localStorage.getItem(KEY) || "";
  } catch {
    return ""; // 隐私模式 / localStorage 被禁时退化为"未选"，不影响使用
  }
}

export function writeRole(id: string) {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* 存不了也要让当前这轮生效 */
  }
  window.dispatchEvent(new CustomEvent(EVT, { detail: id }));
}

export function useRole() {
  const [role, setRole] = useState<string>(readRole);
  useEffect(() => {
    const on = (e: Event) => setRole((e as CustomEvent).detail || "");
    window.addEventListener(EVT, on);
    return () => window.removeEventListener(EVT, on);
  }, []);
  const set = useCallback((id: string) => writeRole(id), []);
  return [role, set] as const;
}
