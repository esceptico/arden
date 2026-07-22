import { beforeEach, describe, expect, it } from "vitest";
import { migrateLegacyStorage } from "../src/lib/brandMigration";

describe("Arden brand migration", () => {
  beforeEach(() => localStorage.clear());

  it("moves legacy storage keys to Arden names", () => {
    localStorage.setItem("ntrp.desktop.prefs", '{"theme":"dark"}');

    expect(migrateLegacyStorage()).toBe(1);
    expect(localStorage.getItem("arden.desktop.prefs")).toBe('{"theme":"dark"}');
    expect(localStorage.getItem("ntrp.desktop.prefs")).toBeNull();
  });

  it("keeps existing Arden values", () => {
    localStorage.setItem("ntrp.desktop.prefs", "legacy");
    localStorage.setItem("arden.desktop.prefs", "current");

    expect(migrateLegacyStorage()).toBe(0);
    expect(localStorage.getItem("arden.desktop.prefs")).toBe("current");
    expect(localStorage.getItem("ntrp.desktop.prefs")).toBeNull();
  });
});
