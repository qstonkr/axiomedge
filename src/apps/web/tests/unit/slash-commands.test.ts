import { describe, it, expect } from "vitest";
import { parseSlash, SLASH_COMMANDS } from "@/components/chat/SlashCommands";

describe("parseSlash", () => {
  it("returns null when no slash", () => {
    expect(parseSlash("hello")).toBeNull();
  });
  it("parses /owner with arg", () => {
    expect(parseSlash("/owner 김철수")).toEqual({ cmd: "owner", arg: "김철수" });
  });
  it("returns prefix-only match for autocomplete", () => {
    expect(parseSlash("/own")).toEqual({ cmd: "own", arg: "" });
  });
});

describe("SLASH_COMMANDS", () => {
  it("includes owner/kb/시간", () => {
    const names = SLASH_COMMANDS.map((c) => c.name);
    expect(names).toContain("owner");
    expect(names).toContain("kb");
    expect(names).toContain("시간");
  });
});
