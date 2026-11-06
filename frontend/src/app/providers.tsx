"use client";

import * as Tip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { SmoothScroll } from "@/components/motion/smooth-scroll";
import { LanguageProvider, type Lang } from "@/lib/i18n";

export function Providers({ children, lang }: { children: ReactNode; lang: Lang }) {
  const [client] = useState(() => new QueryClient({
    defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false } },
  }));
  return (
    <LanguageProvider initial={lang}>
      <QueryClientProvider client={client}>
        <Tip.Provider>
          <SmoothScroll>{children}</SmoothScroll>
        </Tip.Provider>
      </QueryClientProvider>
    </LanguageProvider>
  );
}
