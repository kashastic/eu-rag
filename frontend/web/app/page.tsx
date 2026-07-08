"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  // the app is anonymous-friendly; everyone lands in the chat
  useEffect(() => {
    router.replace("/chat");
  }, [router]);
  return null;
}
