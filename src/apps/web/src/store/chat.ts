"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { Turn } from "@/components/chat/types";

export type ChatMode = "agentic" | "fast";

type ChatStore = {
  selectedKbIds: string[];
  mode: ChatMode;
  turns: Turn[];
  setSelectedKbIds: (ids: string[]) => void;
  toggleKb: (id: string) => void;
  setMode: (mode: ChatMode) => void;
  appendTurn: (turn: Turn) => void;
  clearTurns: () => void;
};

/**
 * /chat sticky state — 사용자가 새로고침해도 KB 선택, mode, 대화
 * history (turns) 가 유지되도록 sessionStorage 에 persist.
 *
 * **왜 sessionStorage** (localStorage 아닌):
 * - 브라우저 탭 닫으면 자동 삭제 → 공용 PC 에서도 안전
 * - 새로고침 / 같은 탭 안의 다른 페이지로 이동 후 복귀 시엔 유지 (의도)
 * - localStorage 는 영속이라 답변 chunks 같은 민감 정보가 long-term 잔존
 *
 * 서버 측 search/agentic_trace 가 search_log 에 저장은 별개 — 프론트
 * 화면 즉시 복원에는 클라이언트 캐시가 더 빠르고 backend 부담 없음.
 */
export const useChatStore = create<ChatStore>()(
  persist(
    (set) => ({
      selectedKbIds: [],
      mode: "agentic",
      turns: [],
      setSelectedKbIds: (ids) => set({ selectedKbIds: ids }),
      toggleKb: (id) =>
        set((s) => ({
          selectedKbIds: s.selectedKbIds.includes(id)
            ? s.selectedKbIds.filter((x) => x !== id)
            : [...s.selectedKbIds, id],
        })),
      setMode: (mode) => set({ mode }),
      appendTurn: (turn) => set((s) => ({ turns: [...s.turns, turn] })),
      clearTurns: () => set({ turns: [] }),
    }),
    {
      name: "axiomedge:chat",
      storage: createJSONStorage(() => sessionStorage),
      // version 올리면 stored shape 호환 안 될 때 이전 데이터 무시.
      version: 1,
      // sessionStorage 5MB 한계 보호 — agentic answer + chunks 가 큰
      // payload 라 50+ turn 이면 quota 위험. 마지막 30 turn 만 persist
      // (in-memory state 는 그대로, persist 시점에만 trim).
      partialize: (state) => ({
        selectedKbIds: state.selectedKbIds,
        mode: state.mode,
        turns: state.turns.slice(-30),
      }),
    },
  ),
);
