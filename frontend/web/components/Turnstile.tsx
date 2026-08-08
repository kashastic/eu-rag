"use client";

// Cloudflare Turnstile, explicit-render mode. The sitekey arrives at runtime
// from /healthz (no build-time env), the script is injected once per page,
// and tokens are single-use — the parent calls reset() after spending one.

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
} from "react";

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      reset: (id: string) => void;
      remove: (id: string) => void;
    };
  }
}

const SCRIPT_SRC =
  "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

let scriptPromise: Promise<void> | null = null;
function loadScript(): Promise<void> {
  if (window.turnstile) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = SCRIPT_SRC;
      s.async = true;
      s.onload = () => resolve();
      document.head.appendChild(s);
    });
  }
  return scriptPromise;
}

export type TurnstileHandle = { reset: () => void };

export const Turnstile = forwardRef<
  TurnstileHandle,
  { sitekey: string; onToken: (token: string | null) => void }
>(function Turnstile({ sitekey, onToken }, ref) {
  const container = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  // latest callback without re-rendering the widget on parent re-renders
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;

  useEffect(() => {
    let disposed = false;
    loadScript().then(() => {
      if (disposed || !container.current || !window.turnstile) return;
      widgetId.current = window.turnstile.render(container.current, {
        sitekey,
        callback: (token: string) => onTokenRef.current(token),
        "expired-callback": () => onTokenRef.current(null),
        "error-callback": () => onTokenRef.current(null),
        theme: "light",
        appearance: "always",
      });
    });
    return () => {
      disposed = true;
      if (widgetId.current && window.turnstile) {
        window.turnstile.remove(widgetId.current);
        widgetId.current = null;
      }
    };
  }, [sitekey]);

  useImperativeHandle(ref, () => ({
    reset() {
      onTokenRef.current(null);
      if (widgetId.current && window.turnstile) {
        window.turnstile.reset(widgetId.current);
      }
    },
  }));

  return <div ref={container} className="turnstile" />;
});
