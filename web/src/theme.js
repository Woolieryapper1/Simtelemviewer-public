import { useSyncExternalStore } from "react";

// Light / dark theme. The choice is "system" (follow the computer's setting),
// "light" or "dark", remembered in this browser. A forced choice goes on
// <html data-theme="...">, which tokens.css uses to pick the colours; with
// "system" the attribute is taken away and the CSS follows the computer.
//
// Charts read their colours from the CSS when they draw, so they need to
// redraw when the theme changes: listen with onThemeChange(), or use
// useTheme() in a component and put `resolved` in the chart's redraw deps.

const KEY = "theme";
const CHOICES = ["system", "light", "dark"];
const media = window.matchMedia?.("(prefers-color-scheme: light)");
const listeners = new Set();

function load() {
  try {
    const saved = localStorage.getItem(KEY);
    return CHOICES.includes(saved) ? saved : "system";
  } catch {
    return "system"; // storage blocked: just follow the computer
  }
}

let choice = load();

// The theme actually showing: "light" or "dark"
const resolve = () => (choice !== "system" ? choice : media?.matches ? "light" : "dark");
let snapshot = { choice, resolved: resolve() };

function apply() {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  snapshot = { choice, resolved: resolve() };
  for (const fn of listeners) fn(snapshot);
}

export function setTheme(next) {
  choice = CHOICES.includes(next) ? next : "system";
  try { localStorage.setItem(KEY, choice); } catch { /* storage off: just not remembered */ }
  apply();
}

export const getTheme = () => snapshot;

// Call fn({ choice, resolved }) whenever the theme changes; returns a function to stop
export function onThemeChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// { choice, resolved } for a React component; it re-renders when the theme changes
export function useTheme() {
  return useSyncExternalStore(onThemeChange, getTheme);
}

// The computer switching between light and dark matters while following it
media?.addEventListener?.("change", () => { if (choice === "system") apply(); });

apply(); // put the saved choice on the page straight away, before anything draws
