"use client";

// "Continue with Google", rendered by Google Identity Services.
//
// This is the ID-token flow, not the authorization-code flow: Google hands the
// browser a short-lived signed JWT and we POST it to /auth/google, which
// verifies the signature, the audience and the issuer before minting our own
// session. There is no client secret anywhere in the system and no callback
// route to get wrong — see core/security/google_oauth.py.
//
// The client id arrives at runtime from /healthz, the same way the Turnstile
// sitekey does, so enabling Google sign-in is an env change on the server and
// not a frontend rebuild.

import { useCallback, useEffect, useRef, useState } from "react";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (opts: Record<string, unknown>) => void;
          renderButton: (el: HTMLElement, opts: Record<string, unknown>) => void;
        };
      };
    };
  }
}

const SCRIPT_SRC = "https://accounts.google.com/gsi/client";
const SCRIPT_TIMEOUT_MS = 10_000;

let scriptPromise: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.google?.accounts?.id) return Promise.resolve();
  if (!scriptPromise) {
    scriptPromise = new Promise<void>((resolve, reject) => {
      const fail = (why: string) => {
        scriptPromise = null; // let a later mount retry
        reject(new Error(why));
      };
      const timer = setTimeout(() => fail("timed out"), SCRIPT_TIMEOUT_MS);
      const script = document.createElement("script");
      script.src = SCRIPT_SRC;
      script.async = true;
      script.onload = () => {
        clearTimeout(timer);
        window.google?.accounts?.id ? resolve() : fail("loaded without the GIS API");
      };
      script.onerror = () => {
        clearTimeout(timer);
        fail("blocked or unreachable");
      };
      document.head.appendChild(script);
    });
  }
  return scriptPromise;
}

export function GoogleSignIn({
  clientId,
  onCredential,
  onError,
}: {
  clientId: string;
  /** The Google ID token. Hand it straight to the API — it is verified there,
   *  never here; nothing the browser checks would be trustworthy anyway. */
  onCredential: (credential: string) => void | Promise<void>;
  onError: (message: string) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  // keep the latest callbacks without re-rendering Google's button
  const credentialRef = useRef(onCredential);
  credentialRef.current = onCredential;
  const errorRef = useRef(onError);
  errorRef.current = onError;

  const render = useCallback(async () => {
    await loadScript();
    if (!container.current || !window.google) return;
    window.google.accounts.id.initialize({
      client_id: clientId,
      callback: (res: { credential?: string }) => {
        if (res.credential) credentialRef.current(res.credential);
        else errorRef.current("Google returned no credential — please try again.");
      },
      // no auto-prompt: signing in is the user's decision, and One Tap
      // appearing over a chat the user is reading is its own annoyance
      auto_select: false,
      cancel_on_tap_outside: true,
    });
    window.google.accounts.id.renderButton(container.current, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "continue_with",
      shape: "rectangular",
      width: 320,
    });
  }, [clientId]);

  useEffect(() => {
    let disposed = false;
    render().catch(() => {
      if (!disposed) setFailed(true);
    });
    return () => {
      disposed = true;
    };
  }, [render]);

  if (failed) {
    // same principle as the Turnstile widget: say why rather than leave a
    // button-shaped hole the user can click forever
    return (
      <p className="hint">
        Google sign-in couldn&apos;t load — an ad blocker may be blocking
        accounts.google.com. Use a username and password instead.
      </p>
    );
  }
  return <div className="google-btn" ref={container} />;
}
