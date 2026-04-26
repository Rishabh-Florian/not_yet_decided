"use client";
import { QueryClientProvider } from "@tanstack/react-query";
import LenisProvider from "@/components/motion/LenisProvider";
import { queryClient } from "@/lib/query-client";

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <LenisProvider />
      {children}
    </QueryClientProvider>
  );
}
