"use client";

import { useEffect } from "react";

/**
 * The static export cannot redirect on the server. Keep `/` as a convenient
 * alias for the team view by redirecting in the browser.
 */
export default function Home() {
  useEffect(() => {
    window.location.replace("/team");
  }, []);

  return (
    <main className="stage grid min-h-dvh place-items-center">
      <p className="label">Opening the team view</p>
    </main>
  );
}
