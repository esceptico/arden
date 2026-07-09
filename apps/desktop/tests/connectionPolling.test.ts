import { beforeEach, expect, test } from "bun:test";
import { pollServerConnectionOnce } from "@/actions/bootstrap";
import { getState, setState } from "@/stores";

beforeEach(() => {
  setState({
    connected: false,
    error: null,
    projects: [],
    sessions: [],
  });
});

test("server connection poll refreshes when disconnected", async () => {
  let refreshes = 0;

  await pollServerConnectionOnce(async () => {
    refreshes += 1;
    getState().setConnected(true);
  });

  expect(refreshes).toBe(1);
  expect(getState().connected).toBe(true);
});

test("server connection poll is idle while already connected", async () => {
  getState().setConnected(true);
  let refreshes = 0;

  await pollServerConnectionOnce(async () => {
    refreshes += 1;
  });

  expect(refreshes).toBe(0);
});
