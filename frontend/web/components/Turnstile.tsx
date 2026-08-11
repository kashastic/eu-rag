"use client";

// Cloudflare Turnstile in the mode production sites actually use: the widget is
// rendered invisibly on mount and *executed* when a form is submitted. It only
// becomes visible if Cloudflare decides this particular visitor has to interact
// ("interaction-only"), so the ordinary visitor never sees a checkbox and the
// composer keeps its space.
//
// Two rules fall out of that and both are load-bearing:
//   1. **No button is ever gated on a challenge.** The old widget solved on page
//      load and Ask/Create-account stayed disabled until it did — so an ad
//      blocker, a sitekey/domain mismatch, or a Cloudflare hiccup left a dead
//      button with no error text. Callers now ask for a token at submit time and
//      surface `TurnstileUnavailableError` if one can't be produced.
//   2. **Every getToken() runs a fresh challenge** (reset then execute) because
//      Turnstile tokens are single-use and expire ~5 minutes after issue.
//
// The sitekey arrives at runtime from /healthz (no build-time env), and the
// script is injected once per page.

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      execute: (id: string, opts?: Record<string, unknown>) => void;
      reset: (id: string) => void;
      remove: (id: string) => void;
    };
  }
}

const SCRIPT_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
const SCRIPT_TIMEOUT_MS = 10_000;
// Backstop for a challenge that never calls back at all. Generous because an
// interactive challenge is waiting on a person noticing a widget that just
// appeared — Cloudflare's own token lifetime is 300s, so stay under that.
const CHALLENGE_TIMEOUT_MS = 120_000;

/** The challenge could not be run — script blocked, sitekey/domain mismatch,
 *  Cloudflare unreachable, or the visitor abandoned an interactive challenge.
 *  Carries a visitor-facing `message` and a `reason` for the console. */
export class TurnstileUnavailableError extends Error {
  constructor(public readonly reason: string) {
    super(
      "Couldn't complete the Cloudflare check. An ad blocker or privacy " +
        "extension may be blocking challenges.cloudflare.com — allow it for " +
        "this site and try again."
    );
    this.name = "TurnstileUnavailableError";
  }
}

let scriptPromise: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (typeof window === "undefined") {
    return Promise.reject(new TurnstileUnavailableError("no window"));
  }
  if (window.turnstile) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise<void>((resolve, reject) => {
      const fail = (why: string) => {
        // clear the cache so a later submit can retry — blocks are often
        // transient (flaky network, a proxy that only sometimes intercepts)
        scriptPromise = null;
        reject(new TurnstileUnavailableError(why));
      };
      const timer = setTimeout(() => fail("script load timed out"), SCRIPT_TIMEOUT_MS);
      const script = document.createElement("script");
      script.src = SCRIPT_SRC;
      script.async = true;
      script.onload = () => {
        clearTimeout(timer);
        // a proxy can serve 200 with a body that isn't the API
        if (window.turnstile) resolve();
        else fail("script loaded but window.turnstile is missing");
      };
      script.onerror = () => {
        clearTimeout(timer);
        fail("script blocked or unreachable");
      };
      document.head.appendChild(script);
    });
  }
  return scriptPromise;
}

export type TurnstileHandle = {
  /** Run a challenge and resolve its single-use token. Rejects with
   *  {@link TurnstileUnavailableError} when no token can be produced. */
  getToken: () => Promise<string>;
};

type Pending = {
  resolve: (token: string) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

export const Turnstile = forwardRef<
  TurnstileHandle,
  {
    sitekey: string;
    /** Fires when Cloudflare puts an interactive challenge on screen (and again
     *  when it comes down), so the caller can explain what the visitor is
     *  waiting on instead of showing its own progress text over the widget. */
    onInteractive?: (active: boolean) => void;
  }
>(function Turnstile({ sitekey, onInteractive }, ref) {
  const container = useRef<HTMLDivElement>(null);
  // resolves to the widget id; rejects if the widget could never be rendered
  const widget = useRef<Promise<string> | null>(null);
  const pending = useRef<Pending | null>(null);
  // Drives the container's footprint via the `active` class: `.turnstile` is
  // height:0/overflow:hidden and only `.turnstile.active` takes up room (see
  // globals.css). This state is therefore the ONLY thing that decides whether
  // the widget occupies space — deliberately, because Cloudflare leaves its
  // ~65px success state in the container after a solved challenge, and reading
  // the space back from the iframe is not something we can do.
  //
  // Every terminal outcome must clear it or the composer keeps a dead widget
  // above it for the rest of the session: `settle()` does that for resolve,
  // error, expiry and timeout, and after-interactive-callback covers a
  // challenge that comes down without settling.
  const [interactive, setInteractive] = useState(false);
  // True from the moment a challenge is requested until it settles. The
  // container is given room for this whole window, not just while `interactive`
  // is set, so a challenge that paints WITHOUT firing
  // before-interactive-callback is still visible and solvable. Relying on that
  // callback for visibility would turn a missed callback into an invisible
  // challenge the visitor cannot solve — a hung submit, which is worse than the
  // leftover widget this collapse exists to remove. It costs nothing on the
  // ordinary path: Cloudflare's non-interactive content is 0px tall, so "give
  // it room" and "collapsed" render identically.
  const [busy, setBusy] = useState(false);
  const onInteractiveRef = useRef(onInteractive);
  onInteractiveRef.current = onInteractive;

  const setChallengeVisible = useCallback((active: boolean) => {
    setInteractive(active);
    onInteractiveRef.current?.(active);
  }, []);

  // Turnstile's callbacks are set once at render() time, so they resolve
  // whichever getToken() call is currently in flight rather than a captured one.
  const settle = useCallback(
    (apply: (p: Pending) => void) => {
      const current = pending.current;
      if (!current) return;
      pending.current = null;
      clearTimeout(current.timer);
      // a timed-out or errored challenge may never send after-interactive.
      // Both flags drop here, which is what takes the widget's space back:
      // Cloudflare leaves its success state in the container after a solved
      // challenge and never reclaims it itself.
      setChallengeVisible(false);
      setBusy(false);
      apply(current);
    },
    [setChallengeVisible]
  );

  useEffect(() => {
    let disposed = false;
    const rendered = loadScript().then(() => {
      if (disposed || !container.current || !window.turnstile) {
        throw new TurnstileUnavailableError("unmounted before render");
      }
      return window.turnstile.render(container.current, {
        sitekey,
        // invisible unless this visitor is actually asked to interact
        appearance: "interaction-only",
        // don't challenge on page load — wait for execute() at submit time
        execution: "execute",
        theme: "light",
        callback: (token: string) => settle((p) => p.resolve(token)),
        "error-callback": (code?: string) =>
          settle((p) => p.reject(new TurnstileUnavailableError(`error ${code ?? "?"}`))),
        "expired-callback": () =>
          settle((p) => p.reject(new TurnstileUnavailableError("token expired"))),
        "timeout-callback": () =>
          settle((p) => p.reject(new TurnstileUnavailableError("challenge timed out"))),
        "before-interactive-callback": () => setChallengeVisible(true),
        "after-interactive-callback": () => setChallengeVisible(false),
      });
    });
    widget.current = rendered;
    // the failure is reported to the visitor by getToken(), not as an
    // unhandled rejection on mount
    rendered.catch(() => {});
    return () => {
      disposed = true;
      widget.current = null;
      rendered.then((id) => window.turnstile?.remove(id)).catch(() => {});
    };
  }, [sitekey, settle, setChallengeVisible]);

  const getToken = useCallback(async () => {
    if (!widget.current) throw new TurnstileUnavailableError("widget not mounted");
    const id = await widget.current;
    if (pending.current) throw new TurnstileUnavailableError("challenge already running");
    return new Promise<string>((resolve, reject) => {
      const timer = setTimeout(
        () => settle((p) => p.reject(new TurnstileUnavailableError("no response"))),
        CHALLENGE_TIMEOUT_MS
      );
      pending.current = { resolve, reject, timer };
      setBusy(true);
      try {
        // tokens are single-use, so every submit starts from a clean widget
        window.turnstile?.reset(id);
        window.turnstile?.execute(id);
      } catch (err) {
        settle((p) => p.reject(new TurnstileUnavailableError(String(err))));
      }
    });
  }, [settle]);

  useImperativeHandle(ref, () => ({ getToken }), [getToken]);

  return (
    <div
      ref={container}
      className={
        "turnstile" +
        (busy || interactive ? " open" : "") +
        (interactive ? " active" : "")
      }
    />
  );
});
