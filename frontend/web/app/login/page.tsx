"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// /login is kept as a shareable URL, but the sign-in UI itself lives in one
// place: the modal on /chat. This page used to be a second copy of that form
// and had drifted — it posted /auth/register with no Turnstile token and no
// widget to get one, so account creation here was a guaranteed 403 on any
// deployment with the bot gate configured. Redirect instead of duplicating.
export default function LoginPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/chat?auth=login");
  }, [router]);
  return null;
}
