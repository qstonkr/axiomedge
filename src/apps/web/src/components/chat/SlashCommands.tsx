"use client";

export const SLASH_COMMANDS = [
  { name: "owner", help: "/owner <이름> — 오너 정보" },
  { name: "kb", help: "/kb <kb_id> — 특정 KB 강제" },
  { name: "시간", help: "/시간 <범위> — 기간 한정 검색" },
] as const;

export type ParsedSlash = { cmd: string; arg: string } | null;

export function parseSlash(input: string): ParsedSlash {
  if (!input.startsWith("/")) return null;
  const trimmed = input.slice(1);
  const sp = trimmed.indexOf(" ");
  if (sp === -1) return { cmd: trimmed, arg: "" };
  return { cmd: trimmed.slice(0, sp), arg: trimmed.slice(sp + 1) };
}

export function SlashCommandDropdown({
  query, onPick,
}: {
  query: string;
  onPick: (name: string) => void;
}) {
  const matches = SLASH_COMMANDS.filter((c) => c.name.startsWith(query));
  if (matches.length === 0) return null;
  return (
    <ul
      role="listbox"
      className="absolute bottom-full mb-1 w-full overflow-hidden rounded-md border border-border-default bg-bg-default shadow-lg"
    >
      {matches.map((c) => (
        <li key={c.name}>
          <button
            type="button"
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(c.name);
            }}
            className="block w-full px-3 py-2 text-left text-sm hover:bg-bg-muted"
          >
            <span className="font-medium">/{c.name}</span>
            <span className="ml-2 text-xs text-fg-muted">{c.help}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
