"use client";

import { useState } from "react";
import type { PublicInvestor } from "@/lib/views";

export function BrandMark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-hidden="true" className="shrink-0 text-gold">
      <path d="M24 3 38 9 44 23 38 37 24 45 10 37 4 23 10 9Z" stroke="currentColor" strokeWidth="2" />
      <path d="m14 14 4 3h12l4-3 2 9-5 12-7 4-7-4-5-12Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
      <path d="m16 23 4 1m12-1-4 1m-8 5h8l-4 4Zm4 4v5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Wordmark({ compact = false }: { compact?: boolean }) {
  return (
    <div className="wordmark flex shrink-0 items-center gap-3">
      <BrandMark size={compact ? 34 : 44} />
      <p className="display whitespace-nowrap text-subhead font-semibold leading-tight text-cream">
        The AI Lion&apos;s Den
      </p>
    </div>
  );
}

export function InvestorAvatar({ investor, size = 56, className = "" }: {
  investor: PublicInvestor;
  size?: number;
  className?: string;
}) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const kind = investor.id === "growth" || investor.id === "customer" ? "lioness" : "lion";
  const description = `${investor.name}, ${investor.focus}, ${investor.title}: a fictional ${kind} investor`;
  return (
    <div className={`investor-portrait relative grid shrink-0 place-items-center overflow-hidden ${className}`}
      style={{ width: size, height: size, color: investor.accent }}>
      {failedImage === investor.image ? (
        <span role="img" aria-label={description} className="display font-semibold" style={{ fontSize: size * 0.32 }}>
          {investor.avatar}
        </span>
      ) : (
        // These local optimized assets work with the static export without an image server.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={investor.image} alt={description} width={512} height={512}
          className="h-full w-full object-cover" onError={() => setFailedImage(investor.image)} />
      )}
    </div>
  );
}

export function FocusTag({ investor, className = "" }: {
  investor: PublicInvestor;
  className?: string;
}) {
  return <span className={`focus-tag ${className}`}>{investor.focus}</span>;
}

export function InvestorMini({ investor }: { investor: PublicInvestor }) {
  return (
    <div className="flex min-w-0 items-center gap-4">
      <InvestorAvatar investor={investor} size={64} />
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <p className="text-body font-semibold text-cream">{investor.name}</p>
          <FocusTag investor={investor} />
        </div>
        <p className="mt-1 text-small text-muted">{investor.title}</p>
      </div>
    </div>
  );
}
