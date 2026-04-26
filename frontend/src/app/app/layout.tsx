"use client";
import { useState, useRef, useCallback } from "react";
import TopBar from "@/components/shell/TopBar";
import VfsTree from "@/components/shell/VfsTree";
import LeftFilterPanelMount from "@/components/shell/LeftFilterPanelMount";
import ProvenancePanel from "@/components/shell/ProvenancePanel";
import { Suspense } from "react";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const [leftW, setLeftW] = useState(220);
  const [rightW, setRightW] = useState(260);
  const dragging = useRef<null | "left" | "right">(null);
  const startX = useRef(0);
  const startW = useRef(0);

  const onMouseDown = useCallback((side: "left" | "right", e: React.MouseEvent) => {
    dragging.current = side;
    startX.current = e.clientX;
    startW.current = side === "left" ? leftW : rightW;
    e.preventDefault();
  }, [leftW, rightW]);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragging.current) return;
    const delta = e.clientX - startX.current;
    if (dragging.current === "left") {
      setLeftW(Math.max(160, Math.min(320, startW.current + delta)));
    } else {
      setRightW(Math.max(200, Math.min(400, startW.current - delta)));
    }
  }, []);

  const onMouseUp = useCallback(() => { dragging.current = null; }, []);

  return (
    <div
      className="h-screen flex flex-col bg-bg overflow-hidden"
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      onMouseLeave={onMouseUp}
    >
      <TopBar />
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Left: VFS tree */}
        <div
          className="flex-none flex flex-col border-r border-border-color overflow-hidden"
          style={{ width: leftW }}
        >
          <Suspense>
            <VfsTree />
          </Suspense>
          <LeftFilterPanelMount />
        </div>

        {/* Left resize handle */}
        <div
          className="w-1 flex-none cursor-col-resize hover:bg-accent/40 transition-colors"
          onMouseDown={(e) => onMouseDown("left", e)}
        />

        {/* Center: main content */}
        <div className="flex-1 overflow-auto min-w-0">
          {children}
        </div>

        {/* Right resize handle */}
        <div
          className="w-1 flex-none cursor-col-resize hover:bg-accent/40 transition-colors"
          onMouseDown={(e) => onMouseDown("right", e)}
        />

        {/* Right: provenance panel */}
        <div
          className="flex-none flex flex-col border-l border-border-color overflow-hidden"
          style={{ width: rightW }}
        >
          <ProvenancePanel />
        </div>
      </div>
    </div>
  );
}
