import { expect, test } from "bun:test";
import {
  googleChoiceLabel,
  parseMCPServerImport,
  slackTokenPrefixValid,
} from "@/features/settings/lib/setupAssistant";

test("labels Google setup service choices", () => {
  expect(googleChoiceLabel("gmail")).toBe("Gmail");
  expect(googleChoiceLabel("calendar")).toBe("Google Calendar");
  expect(googleChoiceLabel("google_drive")).toBe("Google Drive");
});

test("validates Slack token prefixes by selected token type", () => {
  expect(slackTokenPrefixValid("slack_bot_token", "xoxb-abc")).toBe(true);
  expect(slackTokenPrefixValid("slack_user_token", "xoxp-abc")).toBe(true);
  expect(slackTokenPrefixValid("slack_bot_token", "xoxp-abc")).toBe(false);
  expect(slackTokenPrefixValid("slack_user_token", "xoxb-abc")).toBe(false);
});

test("parses MCP import in {name, config} form", () => {
  expect(parseMCPServerImport(JSON.stringify({ name: "fs", config: { transport: "stdio", command: "npx" } }))).toEqual({
    name: "fs",
    config: { transport: "stdio", command: "npx" },
  });
});

test("parses MCP import from mcpServers and servers maps", () => {
  expect(parseMCPServerImport(JSON.stringify({ mcpServers: { github: { transport: "http", url: "https://mcp.example" } } }))).toEqual({
    name: "github",
    config: { transport: "http", url: "https://mcp.example" },
  });
  expect(parseMCPServerImport(JSON.stringify({ servers: { local: { transport: "stdio", command: "node" } } }))).toEqual({
    name: "local",
    config: { transport: "stdio", command: "node" },
  });
});

test("parses direct MCP config with name", () => {
  expect(parseMCPServerImport(JSON.stringify({ name: "remote", transport: "http", url: "https://mcp.example" }))).toEqual({
    name: "remote",
    config: { transport: "http", url: "https://mcp.example" },
  });
});

test("rejects ambiguous or malformed MCP imports", () => {
  expect(() => parseMCPServerImport(JSON.stringify({ mcpServers: { one: { transport: "stdio" }, two: { transport: "http" } } }))).toThrow("one server at a time");
  expect(() => parseMCPServerImport(JSON.stringify({ name: "missing" }))).toThrow("requires");
  expect(() => parseMCPServerImport(JSON.stringify({ name: "bad", transport: "ws" }))).toThrow("transport");
});
